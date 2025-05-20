"""Freshdesk Knowledge Base connector implementation for Onyx. (v1.5)"""

import json
import time
import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
from bs4 import BeautifulSoup

from onyx.configs.app_configs import INDEX_BATCH_SIZE
from onyx.configs.constants import DocumentSource
from onyx.connectors.interfaces import GenerateDocumentsOutput, LoadConnector, PollConnector, SecondsSinceUnixEpoch, SlimConnector, GenerateSlimDocumentOutput
from onyx.connectors.models import ConnectorMissingCredentialError, Document, TextSection, SlimDocument
from onyx.indexing.indexing_heartbeat import IndexingHeartbeatInterface
from onyx.utils.logger import setup_logger

logger = setup_logger()

_FRESHDESK_KB_ID_PREFIX = "FRESHDESK_KB_"

# Fields to extract from solution articles
_SOLUTION_ARTICLE_FIELDS_TO_INCLUDE = {
    "id",
    "title",
    "description", # HTML content
    "description_text", # Plain text content
    "folder_id",
    "category_id",
    "status", # 1: Draft, 2: Published
    "tags",
    "thumbs_up",
    "thumbs_down",
    "hits",
    "created_at",
    "updated_at",
}


def _clean_html_content(html_content: str) -> str:
    """
    Cleans HTML content, extracting plain text.
    Uses BeautifulSoup to parse HTML and get text.
    """
    if not html_content:
        return ""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        text_parts = [p.get_text(separator=" ", strip=True) for p in soup.find_all(['p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])]
        if not text_parts:
            return soup.get_text(separator=" ", strip=True)
        return "\n".join(text_parts)
    except Exception as e:
        logger.error(f"Error cleaning HTML with BeautifulSoup: {e}")
        return html_content


def _create_metadata_from_article(article: dict, domain: str, portal_url: str, portal_id: str) -> dict:
    """
    Creates a metadata dictionary from a Freshdesk solution article.
    """
    metadata: dict[str, Any] = {}
    article_id = article.get("id")

    for key, value in article.items():
        if key not in _SOLUTION_ARTICLE_FIELDS_TO_INCLUDE:
            continue
        if value is None or (isinstance(value, list) and not value):  # Skip None or empty lists
            continue
        metadata[key] = value
    
    # Construct URLs
    if article_id:
        # Agent URL (the one with portalId)
        if portal_url and portal_id:
            portal_base = portal_url.rstrip('/')
            metadata["agent_url"] = f"{portal_base}/a/solutions/articles/{article_id}?portalId={portal_id}"
        else:
            logger.warning(f"Could not construct agent_url for article {article_id}: missing portal_url or portal_id.")

        # Public/API Domain URL
        if domain:
            public_portal_base = f"https://{domain.rstrip('/')}"
            metadata["public_url"] = f"{public_portal_base}/a/solutions/articles/{article_id}"
        else:
            logger.warning(f"Could not construct public_url for article {article_id}: missing domain.")
            
    # Convert status number to human-readable string
    status_number = article.get("status")
    if status_number == 1:
        metadata["status_string"] = "Draft"
    elif status_number == 2:
        metadata["status_string"] = "Published"
    else:
        metadata["status_string"] = "Unknown"

    return metadata


def _create_doc_from_article(article: dict, domain: str, portal_url: str, portal_id: str) -> Document:
    """
    Creates an Onyx Document from a Freshdesk solution article.
    ULTRA-SIMPLE VERSION that should work regardless of article format issues.
    """
    try:
        article_id = str(article.get("id", "UNKNOWN"))
        title = article.get("title", "Untitled Article")
        # Use description_text directly if available
        text_content = article.get("description_text", "No content available")
        
        # Just use current time - don't even attempt to parse the date to avoid any issues
        doc_updated_at = datetime.now(timezone.utc)

        document = Document(
            id=_FRESHDESK_KB_ID_PREFIX + article_id,
            sections=[
                TextSection(
                    link=f"https://{domain}/a/solutions/articles/{article_id}",
                    text=text_content,
                )
            ],
            source=DocumentSource.FRESHDESK_KB,
            semantic_identifier=title,
            metadata={"raw_article_id": article_id},
            doc_updated_at=doc_updated_at,
        )
        
        # Don't even log here to avoid any possible issues
        return document
    except Exception as e:
        # Ultra-simple fallback that cannot fail
        article_id = "ERROR" + str(time.time())
        return Document(
            id=_FRESHDESK_KB_ID_PREFIX + article_id,
            sections=[TextSection(link="", text="Error processing article")],
            source=DocumentSource.FRESHDESK_KB,
            semantic_identifier="Error Document",
            metadata={"error": "Document creation failed"},
            doc_updated_at=datetime.now(timezone.utc),
        )


class FreshdeskKnowledgeBaseConnector(LoadConnector, PollConnector, SlimConnector):
    """
    Onyx Connector for fetching Freshdesk Knowledge Base (Solution Articles) from a specific folder.
    Implements LoadConnector for full indexing and PollConnector for incremental updates.
    """
    def __init__(
        self, 
        freshdesk_folder_id: Optional[str] = None,
        freshdesk_domain: Optional[str] = None,
        freshdesk_api_key: Optional[str] = None,
        freshdesk_portal_url: Optional[str] = None,
        freshdesk_portal_id: Optional[str] = None,
        batch_size: int = INDEX_BATCH_SIZE,
        connector_specific_config: Optional[dict] = None,
        **kwargs
    ) -> None:
        """
        Initialize the Freshdesk Knowledge Base connector.
        
        Args:
            freshdesk_folder_id: The ID of the folder to fetch articles from
            freshdesk_domain: Freshdesk domain (e.g., "company.freshdesk.com")
            freshdesk_api_key: API key for authentication
            freshdesk_portal_url: Optional URL for agent portal links
            freshdesk_portal_id: Optional ID for agent portal links
            batch_size: Number of documents to process in each batch
            connector_specific_config: Configuration specific to this connector
        """
        self.batch_size = batch_size
        self.api_key = freshdesk_api_key
        self.domain = freshdesk_domain
        self.password = "X"  # Freshdesk uses API key as username, 'X' as password
        
        # Store connector_specific_config for later use
        self.connector_specific_config = connector_specific_config
        
        # Get folder_id from constructor parameter or from connector_specific_config
        self.folder_id = freshdesk_folder_id
        if connector_specific_config:
            logger.info(f"connector_specific_config keys: {list(connector_specific_config.keys())}")
            
        if not self.folder_id and connector_specific_config and "freshdesk_folder_id" in connector_specific_config:
            self.folder_id = connector_specific_config.get("freshdesk_folder_id")
            logger.info(f"Using folder_id from connector_specific_config: {self.folder_id}")
        
        # Check for multi-folder configuration
        if connector_specific_config and "freshdesk_folder_ids" in connector_specific_config:
            folder_ids_value = connector_specific_config.get("freshdesk_folder_ids")
            if isinstance(folder_ids_value, list):
                self.folder_ids = folder_ids_value
                logger.info(f"Using folder_ids (list) from connector_specific_config: {self.folder_ids}")
            elif isinstance(folder_ids_value, str):
                self.folder_ids = folder_ids_value  # Store as string, will be parsed in load_from_state/poll_source
                logger.info(f"Using folder_ids (string) from connector_specific_config: {self.folder_ids}")
        
        # Debug connector initialization params
        logger.info(f"Initializing Freshdesk KB connector with params:")
        logger.info(f"  - folder_id: {self.folder_id}")
        logger.info(f"  - domain: {self.domain}")
        logger.info(f"  - api_key: {'*****' + self.api_key[-4:] if self.api_key else None}")
        
        # Optional portal params
        self.portal_url = freshdesk_portal_url
        if not self.portal_url and connector_specific_config and "freshdesk_portal_url" in connector_specific_config:
            self.portal_url = connector_specific_config.get("freshdesk_portal_url")
            
        self.portal_id = freshdesk_portal_id
        if not self.portal_id and connector_specific_config and "freshdesk_portal_id" in connector_specific_config:
            self.portal_id = connector_specific_config.get("freshdesk_portal_id")
        
        self.headers = {"Content-Type": "application/json"}
        self.base_url = f"https://{self.domain}/api/v2" if self.domain else None
        self.auth = (self.api_key, self.password) if self.api_key else None

    def load_credentials(self, credentials: dict[str, str | int]) -> None:
        """Loads Freshdesk API credentials and configuration."""
        api_key = credentials.get("freshdesk_api_key")
        domain = credentials.get("freshdesk_domain")
        portal_url = credentials.get("freshdesk_portal_url")  # For constructing agent URLs
        portal_id = credentials.get("freshdesk_portal_id")    # For constructing agent URLs
        
        # Check credentials
        if not all(isinstance(cred, str) for cred in [domain, api_key] if cred is not None):
            missing = [
                name for name, val in {
                    "domain": domain, "api_key": api_key,
                }.items() if not isinstance(val, str)
            ]
            raise ConnectorMissingCredentialError(
                f"Required Freshdesk KB credentials must be strings. Missing/invalid: {missing}"
            )

        self.api_key = str(api_key)
        self.domain = str(domain)
        # Handle optional parameters
        self.portal_url = str(portal_url) if portal_url is not None else None
        self.portal_id = str(portal_id) if portal_id is not None else None
        self.base_url = f"https://{self.domain}/api/v2"
        self.auth = (self.api_key, self.password)
        
        # Log that credentials were loaded
        logger.info(f"CREDENTIALS LOADED: domain={self.domain}, api_key={'*****' + self.api_key[-4:] if self.api_key else None}")

    def validate_connector_settings(self) -> None:
        """
        Validate connector settings by testing API connectivity.
        """
        # Critical validation - check for domain and API key
        if not self.domain:
            logger.error("CRITICAL ERROR: Missing Freshdesk domain - check credentials!")
            raise ConnectorMissingCredentialError(
                "Missing required Freshdesk domain in credentials"
            )
            
        if not self.api_key:
            logger.error("CRITICAL ERROR: Missing Freshdesk API key - check credentials!")
            raise ConnectorMissingCredentialError(
                "Missing required Freshdesk API key in credentials"
            )

        # Collect all configured folder IDs for validation
        folder_ids = []
        
        # Check if we have a single folder_id
        if self.folder_id:
            folder_ids.append(self.folder_id)
        
        # Check for folder_ids in class properties or connector_specific_config
        if hasattr(self, 'folder_ids'):
            if isinstance(self.folder_ids, list):
                folder_ids.extend(self.folder_ids)
            elif isinstance(self.folder_ids, str):
                parsed_ids = [fid.strip() for fid in self.folder_ids.split(',') if fid.strip()]
                folder_ids.extend(parsed_ids)
        
        # Also check connector_specific_config directly
        if self.connector_specific_config and "freshdesk_folder_ids" in self.connector_specific_config:
            folder_ids_value = self.connector_specific_config.get("freshdesk_folder_ids")
            if isinstance(folder_ids_value, list):
                folder_ids.extend(folder_ids_value)
            elif isinstance(folder_ids_value, str):
                parsed_ids = [fid.strip() for fid in folder_ids_value.split(',') if fid.strip()]
                folder_ids.extend(parsed_ids)
        
        # We need at least one folder ID for validation
        if not folder_ids:
            logger.error("No folder IDs found in connector settings")
            raise ConnectorMissingCredentialError(
                "Missing folder ID(s) in connector settings. Please configure at least one folder ID."
            )
            
        # Use the first folder ID for validation
        validation_folder_id = folder_ids[0]
        logger.info(f"Using folder ID {validation_folder_id} for validation (out of {len(folder_ids)} configured folders)")
        
        # Log validation attempt with parameters for debugging
        logger.info(f"Validating Freshdesk KB connector with:")
        logger.info(f"  domain: {self.domain}")
        logger.info(f"  validation folder_id: {validation_folder_id}")
        logger.info(f"  total configured folders: {len(folder_ids)}")
        logger.info(f"  api_key present: {'Yes' if self.api_key else 'No'}")
        logger.info(f"  base_url: {self.base_url}")
        
        try:
            # Test API by trying to fetch one article from the validation folder
            url = f"{self.base_url}/solutions/folders/{validation_folder_id}/articles"
            params = {"page": 1, "per_page": 1}
            
            logger.info(f"Making validation request to: {url}")
            response = requests.get(url, auth=self.auth, headers=self.headers, params=params)
            
            # Log the response for debugging
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    logger.info(f"Validation successful - got {len(data)} articles in response")
                    if len(data) > 0:
                        # Log details of first article
                        logger.info(f"First article: id={data[0].get('id')}, title={data[0].get('title')}")
                else:
                    logger.warning(f"Unexpected response format: {type(data)}")
            
            response.raise_for_status()
            logger.info(f"Successfully validated Freshdesk KB connector for folder {validation_folder_id}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to validate Freshdesk KB connector: {e}")
            logger.error(f"Response: {response.text if 'response' in locals() else 'No response'}")
            if 'response' in locals():
                logger.error(f"Status code: {response.status_code}")
            raise ConnectorMissingCredentialError(
                f"Could not connect to Freshdesk API: {e}"
            )

    def _make_api_request(self, url: str, params: Optional[Dict[str, Any]] = None) -> Optional[List[Dict[str, Any]]]:
        """Makes a GET request to the Freshdesk API with rate limit handling."""
        if not self.auth:
            raise ConnectorMissingCredentialError("Freshdesk KB credentials not loaded.")
        
        # Verify the URL doesn't have duplicated domains (which could cause SSL errors)
        if ".freshdesk.com.freshdesk.com" in url:
            url = url.replace(".freshdesk.com.freshdesk.com", ".freshdesk.com")
            logger.warning(f"Fixed malformed URL containing duplicate domain: {url}")
        
        retries = 3
        for attempt in range(retries):
            try:
                response = requests.get(url, auth=self.auth, headers=self.headers, params=params)
                response.raise_for_status()

                if response.status_code == 429:  # Too Many Requests
                    retry_after = int(response.headers.get("Retry-After", 60))
                    logger.warning(f"Rate limit exceeded. Retrying after {retry_after} seconds.")
                    time.sleep(retry_after)
                    continue
                
                return response.json()
            except requests.exceptions.HTTPError as e:
                logger.error(f"HTTP error: {e} - {response.text if 'response' in locals() else 'No response'} for URL {url} with params {params}")
                return None
            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed: {e} for URL {url}")
                if attempt < retries - 1:
                    time.sleep(5 * (attempt + 1))
                else:
                    return None
        return None
        
    def list_available_folders(self) -> List[Dict[str, Any]]:
        """
        Lists all available Knowledge Base folders from Freshdesk.
        Returns a list of folder details that can be used for configuration.
        """
        if not self.base_url:
            raise ConnectorMissingCredentialError("Freshdesk KB connector not properly configured (base_url missing).")
        
        all_folders = []
        
        try:
            # First fetch all solution categories
            categories_url = f"{self.base_url}/solutions/categories"
            categories = self._make_api_request(categories_url)
            
            if not categories or not isinstance(categories, list):
                logger.error("Failed to fetch solution categories or unexpected response format")
                return []
            
            # For each category, get its folders
            logger.info(f"Found {len(categories)} solution categories")
            for category in categories:
                category_id = category.get("id")
                category_name = category.get("name", "Unknown")
                
                if not category_id:
                    continue
                
                # Fetch folders for this category
                folders_url = f"{self.base_url}/solutions/categories/{category_id}/folders"
                folders = self._make_api_request(folders_url)
                
                if not folders or not isinstance(folders, list):
                    logger.warning(f"Failed to fetch folders for category {category_id} or empty response")
                    continue
                
                logger.info(f"Found {len(folders)} folders in category '{category_name}'")
                
                # Add category context to each folder
                for folder in folders:
                    folder["category_name"] = category_name
                    all_folders.append(folder)
                
                # Respect rate limits
                time.sleep(1)
            
            logger.info(f"Total folders found: {len(all_folders)}")
            return all_folders
            
        except Exception as e:
            logger.error(f"Error listing available folders: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def _fetch_articles_from_folder(self, folder_id: str, updated_since: Optional[datetime] = None) -> Iterator[List[dict]]:
        """
        Fetches solution articles from a specific folder, handling pagination.
        Filters by 'updated_since' if provided.
        """
        if not self.base_url or not folder_id:
            raise ConnectorMissingCredentialError("Freshdesk KB connector not properly configured (base_url or folder_id missing).")

        page = 1
        while True:
            url = f"{self.base_url}/solutions/folders/{folder_id}/articles"
            params: dict[str, Any] = {"page": page, "per_page": 30}

            logger.info(f"Fetching articles from Freshdesk KB folder {folder_id}, page {page}...")
            article_batch = self._make_api_request(url, params)

            if article_batch is None:  # Error occurred
                logger.error(f"Failed to fetch articles for folder {folder_id}, page {page}.")
                break
            
            if not isinstance(article_batch, list):
                logger.error(f"Unexpected API response format for articles: {type(article_batch)}. Expected list.")
                break

            if not article_batch:  # No more articles
                logger.info(f"No more articles found for folder {folder_id} on page {page}.")
                break
            
            # If updated_since is provided, filter locally
            if updated_since:
                filtered_batch = []
                for article in article_batch:
                    if article.get("updated_at"):
                        article_updated_at = datetime.fromisoformat(article["updated_at"].replace("Z", "+00:00"))
                        if article_updated_at >= updated_since:
                            filtered_batch.append(article)
                
                if filtered_batch:
                    logger.info(f"Fetched {len(filtered_batch)} articles updated since {updated_since.isoformat()} from folder {folder_id}, page {page}.")
                    yield filtered_batch
            else:
                logger.info(f"Fetched {len(article_batch)} articles from folder {folder_id}, page {page}.")
                yield article_batch

            if len(article_batch) < params["per_page"]:
                logger.info(f"Last page reached for folder {folder_id}.")
                break
            
            page += 1
            time.sleep(1)  # Basic rate limiting

    def _process_articles(self, folder_ids: List[str], start_time: Optional[datetime] = None) -> GenerateDocumentsOutput:
        """
        Process articles from multiple folders, converting them to Onyx Documents.
        Accepts a list of folder IDs to fetch from.
        """
        if not self.domain:
            raise ConnectorMissingCredentialError("Freshdesk KB domain not loaded.")
            
        logger.info("======= STARTING MULTI-FOLDER ARTICLE PROCESSING =======")
        
        # Handle case where a single folder ID string is passed
        if isinstance(folder_ids, str):
            folder_ids = [folder_ids]
            
        # Make sure we have at least one folder ID
        if not folder_ids:
            logger.error("No folder IDs provided for processing")
            raise ValueError("No folder IDs provided for processing")
            
        logger.info(f"Processing articles from {len(folder_ids)} folders: {folder_ids}")
        
        # Use portal_url and portal_id if available, otherwise use None
        portal_url = self.portal_url if self.portal_url else None
        portal_id = self.portal_id if self.portal_id else None
        
        article_count = 0
        
        try:
            # First, create a document directly - this should always appear in the index 
            # no matter what is happening with the article processing
            canary_doc = Document(
                id=_FRESHDESK_KB_ID_PREFIX + "CANARY_TEST_DOC",
                sections=[TextSection(link="", text="This is a test document to verify indexing")],
                source=DocumentSource.FRESHDESK_KB,
                semantic_identifier="CANARY TEST DOCUMENT",
                metadata={"test": "canary"},
                doc_updated_at=datetime.now(timezone.utc),
            )
            
            # Yield the canary document by itself to ensure it gets indexed
            logger.info("====== YIELDING CANARY DOCUMENT ======")
            yield [canary_doc]
            logger.info("====== CANARY DOCUMENT YIELDED ======")
            
            # Process each folder one by one
            for folder_id in folder_ids:
                logger.info(f"Processing folder ID: {folder_id}")
                folder_article_count = 0
                
                # Process articles in batches for this folder
                for article_list_from_api in self._fetch_articles_from_folder(folder_id, start_time):
                    if not article_list_from_api:
                        logger.info(f"Received empty article batch from folder {folder_id} - skipping")
                        continue
                    
                    logger.info(f"Processing batch of {len(article_list_from_api)} articles from folder {folder_id}")
                    folder_article_count += len(article_list_from_api)
                    article_count += len(article_list_from_api)
                    
                    # Process each batch of articles separately to avoid any cross-batch dependencies
                    current_batch = []
                    
                    for article_data in article_list_from_api:
                        try:
                            article_id = str(article_data.get('id', 'UNKNOWN'))
                            doc = _create_doc_from_article(article_data, self.domain, portal_url, portal_id)
                            current_batch.append(doc)
                            logger.info(f"Added article ID {article_id} to current batch")
                        except Exception as e:
                            logger.error(f"Failed to create document: {e}")
                            # Don't even try to create an error document - just skip it
                    
                    # Yield this batch immediately
                    if current_batch:
                        logger.info(f"====== YIELDING BATCH OF {len(current_batch)} DOCUMENTS ======")
                        yield current_batch
                        logger.info(f"====== BATCH YIELDED SUCCESSFULLY ======")
                
                logger.info(f"Completed processing folder {folder_id} - {folder_article_count} articles indexed")
            
            # Create a final document to confirm we reached the end
            final_doc = Document(
                id=_FRESHDESK_KB_ID_PREFIX + "FINAL_TEST_DOC",
                sections=[TextSection(link="", text=f"This is the final document. Processed {article_count} articles from {len(folder_ids)} folders.")],
                source=DocumentSource.FRESHDESK_KB,
                semantic_identifier="FINAL TEST DOCUMENT",
                metadata={"article_count": article_count, "folder_count": len(folder_ids)},
                doc_updated_at=datetime.now(timezone.utc),
            )
            
            # Yield the final document by itself
            logger.info("====== YIELDING FINAL DOCUMENT ======")
            yield [final_doc]
            logger.info("====== FINAL DOCUMENT YIELDED ======")
            
            logger.info(f"======= COMPLETED PROCESSING {article_count} ARTICLES FROM {len(folder_ids)} FOLDERS =======")
            
        except Exception as e:
            logger.error(f"Critical error in article processing: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
            # Try to yield an error document as a last resort
            error_doc = Document(
                id=_FRESHDESK_KB_ID_PREFIX + "CRITICAL_ERROR",
                sections=[TextSection(link="", text=f"Critical error during processing: {e}")],
                source=DocumentSource.FRESHDESK_KB,
                semantic_identifier="CRITICAL ERROR DOCUMENT",
                metadata={"error": str(e)},
                doc_updated_at=datetime.now(timezone.utc),
            )
            
            logger.info("====== YIELDING ERROR DOCUMENT ======")
            yield [error_doc]
            logger.info("====== ERROR DOCUMENT YIELDED ======")

    def load_from_state(self) -> GenerateDocumentsOutput:
        """Loads all solution articles from the configured folders."""
        # Get folder_ids from connector config
        folder_ids = []
        
        # Check if we have a single folder_id or multiple folder_ids in the configuration
        if hasattr(self, 'folder_id') and self.folder_id:
            # Single folder ID provided directly
            folder_ids.append(self.folder_id)
        
        # Check for folder_ids in connector_specific_config and class attributes
        if hasattr(self, 'connector_specific_config') and self.connector_specific_config:
            # Check for freshdesk_folder_ids in connector_specific_config
            if 'freshdesk_folder_ids' in self.connector_specific_config:
                folder_ids_value = self.connector_specific_config.get('freshdesk_folder_ids')
                if isinstance(folder_ids_value, list):
                    folder_ids.extend(folder_ids_value)
                elif isinstance(folder_ids_value, str):
                    folder_ids.extend([fid.strip() for fid in folder_ids_value.split(',') if fid.strip()])
                logger.info(f"Using folder_ids from connector_specific_config['freshdesk_folder_ids']: {folder_ids}")
        
        # Also check if folder_ids was set as a class attribute
        if hasattr(self, 'folder_ids'):
            if isinstance(self.folder_ids, list):
                # Multiple folder IDs provided as a list
                folder_ids.extend(self.folder_ids)
                logger.info(f"Using folder_ids from self.folder_ids (list): {self.folder_ids}")
            elif isinstance(self.folder_ids, str):
                # Multiple folder IDs provided as a comma-separated string
                parsed_ids = [folder_id.strip() for folder_id in self.folder_ids.split(',') if folder_id.strip()]
                folder_ids.extend(parsed_ids)
                logger.info(f"Using folder_ids from self.folder_ids (string): parsed as {parsed_ids}")
            
        if not folder_ids:
            raise ConnectorMissingCredentialError("No Freshdesk KB folder_id(s) configured for load_from_state.")
            
        # Double check credentials before starting indexing
        if not self.domain or not self.api_key:
            logger.error(f"CRITICAL ERROR: Missing credentials in load_from_state! domain={self.domain}, api_key_present={'Yes' if self.api_key else 'No'}")
            logger.error(f"Base URL: {self.base_url}, Auth: {bool(self.auth)}")
            raise ConnectorMissingCredentialError("Missing required Freshdesk credentials for indexing")
            
        logger.info(f"Loading all solution articles from {len(folder_ids)} Freshdesk KB folders: {folder_ids}")
        logger.info(f"Using domain: {self.domain}")
        
        # Explicitly log that we're starting to yield documents
        logger.info(f"Starting to yield documents from Freshdesk KB folders")
        yield from self._process_articles(folder_ids)

    def poll_source(self, start: SecondsSinceUnixEpoch, end: SecondsSinceUnixEpoch) -> GenerateDocumentsOutput:
        """
        Polls for solution articles updated within the given time range.
        """
        # Get folder_ids from connector config
        folder_ids = []
        
        # Check if we have a single folder_id or multiple folder_ids in the configuration
        if hasattr(self, 'folder_id') and self.folder_id:
            # Single folder ID provided directly
            folder_ids.append(self.folder_id)
        
        # Check for folder_ids in connector_specific_config and class attributes
        if hasattr(self, 'connector_specific_config') and self.connector_specific_config:
            # Check for freshdesk_folder_ids in connector_specific_config
            if 'freshdesk_folder_ids' in self.connector_specific_config:
                folder_ids_value = self.connector_specific_config.get('freshdesk_folder_ids')
                if isinstance(folder_ids_value, list):
                    folder_ids.extend(folder_ids_value)
                elif isinstance(folder_ids_value, str):
                    folder_ids.extend([fid.strip() for fid in folder_ids_value.split(',') if fid.strip()])
                logger.info(f"Poll: Using folder_ids from connector_specific_config['freshdesk_folder_ids']: {folder_ids}")
        
        # Also check if folder_ids was set as a class attribute
        if hasattr(self, 'folder_ids'):
            if isinstance(self.folder_ids, list):
                # Multiple folder IDs provided as a list
                folder_ids.extend(self.folder_ids)
                logger.info(f"Poll: Using folder_ids from self.folder_ids (list): {self.folder_ids}")
            elif isinstance(self.folder_ids, str):
                # Multiple folder IDs provided as a comma-separated string
                parsed_ids = [folder_id.strip() for folder_id in self.folder_ids.split(',') if folder_id.strip()]
                folder_ids.extend(parsed_ids)
                logger.info(f"Poll: Using folder_ids from self.folder_ids (string): parsed as {parsed_ids}")
            
        if not folder_ids:
            raise ConnectorMissingCredentialError("No Freshdesk KB folder_id(s) configured for poll_source.")
            
        # Double check credentials before starting polling
        if not self.domain or not self.api_key:
            logger.error(f"CRITICAL ERROR: Missing credentials in poll_source! domain={self.domain}, api_key_present={'Yes' if self.api_key else 'No'}")
            logger.error(f"Base URL: {self.base_url}, Auth: {bool(self.auth)}")
            raise ConnectorMissingCredentialError("Missing required Freshdesk credentials for polling")
        
        start_datetime = datetime.fromtimestamp(start, tz=timezone.utc)
        
        logger.info(f"Polling {len(folder_ids)} Freshdesk KB folders for updates since {start_datetime.isoformat()}")
        logger.info(f"Using domain: {self.domain}, folders: {folder_ids}")
        yield from self._process_articles(folder_ids, start_datetime)

    def _get_slim_documents_for_article_batch(self, articles: List[Dict[str, Any]]) -> List[SlimDocument]:
        """Convert a batch of articles to SlimDocuments."""
        slim_docs = []
        for article in articles:
            article_id = article.get("id")
            if article_id:
                # All we need is the ID - no permissions data needed for this connector
                slim_docs.append(
                    SlimDocument(
                        id=_FRESHDESK_KB_ID_PREFIX + str(article_id),
                        perm_sync_data=None,
                    )
                )
        return slim_docs

    def retrieve_all_slim_documents(
        self,
        start: SecondsSinceUnixEpoch | None = None,
        end: SecondsSinceUnixEpoch | None = None,
        callback: IndexingHeartbeatInterface | None = None,
    ) -> GenerateSlimDocumentOutput:
        """
        Retrieves all document IDs for pruning purposes.
        """
        # Get folder_ids using same logic as load_from_state and poll_source
        folder_ids = []
        
        # Check if we have a single folder_id or multiple folder_ids in the configuration
        if hasattr(self, 'folder_id') and self.folder_id:
            # Single folder ID provided directly
            folder_ids.append(self.folder_id)
        
        # Check for folder_ids in connector_specific_config and class attributes
        if hasattr(self, 'connector_specific_config') and self.connector_specific_config:
            # Check for freshdesk_folder_ids in connector_specific_config
            if 'freshdesk_folder_ids' in self.connector_specific_config:
                folder_ids_value = self.connector_specific_config.get('freshdesk_folder_ids')
                if isinstance(folder_ids_value, list):
                    folder_ids.extend(folder_ids_value)
                elif isinstance(folder_ids_value, str):
                    folder_ids.extend([fid.strip() for fid in folder_ids_value.split(',') if fid.strip()])
        
        # Also check if folder_ids was set as a class attribute
        if hasattr(self, 'folder_ids'):
            if isinstance(self.folder_ids, list):
                folder_ids.extend(self.folder_ids)
            elif isinstance(self.folder_ids, str):
                parsed_ids = [folder_id.strip() for folder_id in self.folder_ids.split(',') if folder_id.strip()]
                folder_ids.extend(parsed_ids)
            
        if not folder_ids:
            raise ConnectorMissingCredentialError("No Freshdesk KB folder_id(s) configured for slim document retrieval.")
        
        start_datetime = datetime.fromtimestamp(start, tz=timezone.utc) if start else None
        
        # Process each folder
        for folder_id in folder_ids:
            logger.info(f"Retrieving slim documents from folder {folder_id}")
            
            slim_batch: List[SlimDocument] = []
            for article_batch in self._fetch_articles_from_folder(folder_id, start_datetime):
                # Convert to slim documents
                new_slim_docs = self._get_slim_documents_for_article_batch(article_batch)
                slim_batch.extend(new_slim_docs)
                
                # Heartbeat callback if provided
                if callback:
                    callback.heartbeat()
                
                if len(slim_batch) >= self.batch_size:
                    logger.info(f"Yielding batch of {len(slim_batch)} slim documents from folder {folder_id}")
                    yield slim_batch
                    slim_batch = []
            
            if slim_batch:
                logger.info(f"Yielding final batch of {len(slim_batch)} slim documents from folder {folder_id}")
                yield slim_batch
        
        logger.info(f"Completed retrieval of slim documents from {len(folder_ids)} folders")
