"""YouTrack connector implementation for Onyx with multi-project support.
Version 1.2.3 - Enhanced debugging
"""

# Version tracking
YOUTRACK_CONNECTOR_VERSION = "1.2.3"

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

_YOUTRACK_ID_PREFIX = "YOUTRACK_"

# Fields to extract from issues
_ISSUE_FIELDS_TO_INCLUDE = {
    "id",
    "summary",
    "description",
    "created",
    "updated",
    "resolved",
    "customFields",
    "reporter",
    "updater",
    "tags",
    "links",
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


def _create_metadata_from_issue(issue: dict, base_url: str) -> dict:
    """
    Creates a metadata dictionary from a YouTrack issue.
    """
    metadata: dict[str, Any] = {}
    issue_id = issue.get("id")
    
    for key, value in issue.items():
        if key not in _ISSUE_FIELDS_TO_INCLUDE:
            continue
        if value is None or (isinstance(value, list) and not value):  # Skip None or empty lists
            continue
        metadata[key] = value
    
    # Construct URLs
    if issue_id and "idReadable" in issue:
        # Public URL
        readable_id = issue.get("idReadable")
        metadata["issue_url"] = f"{base_url}/issue/{readable_id}"
    
    return metadata


def _create_doc_from_issue(issue: dict, base_url: str) -> Document:
    """
    Creates an Onyx Document from a YouTrack issue.
    """
    try:
        issue_id = str(issue.get("id", "UNKNOWN"))
        readable_id = issue.get("idReadable", "UNKNOWN")
        summary = issue.get("summary", "Untitled Issue")
        
        # Get description as text
        description = issue.get("description", "")
        if description:
            if isinstance(description, dict) and "text" in description:
                description = description.get("text", "")
            text_content = _clean_html_content(description)
        else:
            text_content = "No description available"
        
        # Use creation and update times if available
        doc_created_at = None
        doc_updated_at = None
        
        if "created" in issue:
            try:
                # YouTrack uses milliseconds since epoch
                timestamp = int(issue["created"]) / 1000.0
                doc_created_at = datetime.fromtimestamp(timestamp, timezone.utc)
            except (ValueError, TypeError) as e:
                logger.warning(f"Error parsing issue creation time: {e}")
        
        if "updated" in issue:
            try:
                # YouTrack uses milliseconds since epoch
                timestamp = int(issue["updated"]) / 1000.0
                doc_updated_at = datetime.fromtimestamp(timestamp, timezone.utc)
            except (ValueError, TypeError) as e:
                logger.warning(f"Error parsing issue update time: {e}")
        
        # Default to current time if parsing fails
        if not doc_created_at:
            doc_created_at = datetime.now(timezone.utc)
        if not doc_updated_at:
            doc_updated_at = datetime.now(timezone.utc)
        
        # Create document
        link = f"{base_url}/issue/{readable_id}" if readable_id else ""
        document = Document(
            id=_YOUTRACK_ID_PREFIX + issue_id,
            sections=[
                TextSection(
                    link=link,
                    text=text_content,
                )
            ],
            source=DocumentSource.YOUTRACK,
            semantic_identifier=f"{readable_id}: {summary}",
            metadata=_create_metadata_from_issue(issue, base_url),
            doc_created_at=doc_created_at,
            doc_updated_at=doc_updated_at,
        )
        
        return document
    except Exception as e:
        # Create a basic error document as fallback
        error_id = "ERROR" + str(time.time())
        return Document(
            id=_YOUTRACK_ID_PREFIX + error_id,
            sections=[TextSection(link="", text=f"Error processing issue: {str(e)}")],
            source=DocumentSource.YOUTRACK,
            semantic_identifier="Error Document",
            metadata={"error": "Document creation failed"},
            doc_updated_at=datetime.now(timezone.utc),
        )


class YouTrackConnector(LoadConnector, PollConnector, SlimConnector):
    """
    Onyx Connector for fetching YouTrack issues from specific projects.
    Implements LoadConnector for full indexing and PollConnector for incremental updates.
    """
    def __init__(
        self, 
        youtrack_url: Optional[str] = None,
        youtrack_token: Optional[str] = None,
        youtrack_project_id: Optional[str] = None,
        youtrack_project_ids: Optional[str] = None,
        project_id: Optional[str] = None,  # For alternative field naming
        custom_query: Optional[str] = None,
        include_comments: bool = True,
        batch_size: int = INDEX_BATCH_SIZE,
        connector_specific_config: Optional[dict] = None,
        **kwargs
    ) -> None:
        """
        Initialize the YouTrack connector.
        
        Args:
            youtrack_url: Base URL of the YouTrack instance
            youtrack_token: API token for authentication
            youtrack_project_id: Single project ID (legacy)
            youtrack_project_ids: Comma-separated list of project IDs
            project_id: Alternative field for single project ID
            custom_query: Optional YouTrack query to filter issues
            include_comments: Whether to include issue comments
            batch_size: Number of documents to process in each batch
            connector_specific_config: Configuration specific to this connector
        """
        self.batch_size = batch_size
        self.token = youtrack_token
        self.base_url = youtrack_url
        self.include_comments = include_comments
        self.custom_query = custom_query
        
        # DEBUG: Log all constructor arguments to help debug
        logger.error(f"INITIALIZING YOUTRACK CONNECTOR v{YOUTRACK_CONNECTOR_VERSION}")
        logger.info("INITIALIZING YOUTRACK CONNECTOR WITH ALL PARAMS:")
        logger.info(f"  youtrack_url: {youtrack_url}")
        logger.info(f"  youtrack_token present: {'Yes' if youtrack_token else 'No'}")
        logger.info(f"  youtrack_project_id: {youtrack_project_id}")
        logger.info(f"  youtrack_project_ids: {youtrack_project_ids}")
        logger.info(f"  project_id: {project_id}")
        logger.info(f"  custom_query: {custom_query}")
        logger.info(f"  include_comments: {include_comments}")
        
        if connector_specific_config:
            logger.info(f"CONNECTOR CONFIG: {connector_specific_config}")
        
        # Store connector_specific_config for later use
        self.connector_specific_config = connector_specific_config
        
        # Collect potential project IDs from all possible sources
        # First, check direct parameters
        self.project_id = youtrack_project_id or project_id
        self.project_ids = youtrack_project_ids
        
        # Then check connector_specific_config
        if connector_specific_config:
            logger.info(f"connector_specific_config keys: {list(connector_specific_config.keys())}")
            
            # Check for single project ID
            if not self.project_id and "youtrack_project_id" in connector_specific_config:
                self.project_id = connector_specific_config.get("youtrack_project_id")
                logger.info(f"Using project_id from connector_specific_config['youtrack_project_id']: {self.project_id}")
                
            if not self.project_id and "project_id" in connector_specific_config:
                self.project_id = connector_specific_config.get("project_id")
                logger.info(f"Using project_id from connector_specific_config['project_id']: {self.project_id}")
                
            # Check for multi-project configuration
            if not self.project_ids and "youtrack_project_ids" in connector_specific_config:
                project_ids_value = connector_specific_config.get("youtrack_project_ids")
                if isinstance(project_ids_value, list):
                    self.project_ids = project_ids_value
                    logger.info(f"Using project_ids (list) from connector_specific_config: {self.project_ids}")
                elif isinstance(project_ids_value, str):
                    self.project_ids = project_ids_value  # Store as string, will be parsed in load_from_state/poll_source
                    logger.info(f"Using project_ids (string) from connector_specific_config: {self.project_ids}")
        
        # Debug connector initialization params
        logger.info(f"Initializing YouTrack connector with params:")
        logger.info(f"  - project_id: {self.project_id}")
        logger.info(f"  - base_url: {self.base_url}")
        logger.info(f"  - token present: {'Yes' if self.token else 'No'}")
        
        # Set up headers for API requests
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}" if self.token else "",
        }
        self.api_url = f"{self.base_url}/api" if self.base_url else None
    
    def load_credentials(self, credentials: dict[str, str | int]) -> None:
        """Loads YouTrack API credentials and configuration."""
        token = credentials.get("youtrack_token")
        url = credentials.get("youtrack_url")
        
        # Check credentials
        if not all(isinstance(cred, str) for cred in [url, token] if cred is not None):
            missing = [
                name for name, val in {
                    "url": url, "token": token,
                }.items() if not isinstance(val, str)
            ]
            raise ConnectorMissingCredentialError(
                f"Required YouTrack credentials must be strings. Missing/invalid: {missing}"
            )

        self.token = str(token)
        self.base_url = str(url).rstrip('/')  # Remove trailing slash if present
        self.api_url = f"{self.base_url}/api"
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        
        # Check for project IDs in the credentials
        if "youtrack_project_ids" in credentials:
            project_ids_value = credentials.get("youtrack_project_ids")
            if project_ids_value:
                self.project_ids = str(project_ids_value)
                logger.info(f"Found project_ids in credentials: {self.project_ids}")
        
        # Also check for single project ID (backward compatibility)
        if "youtrack_project_id" in credentials:
            project_id_value = credentials.get("youtrack_project_id")
            if project_id_value:
                self.project_id = str(project_id_value)
                logger.info(f"Found single project_id in credentials: {self.project_id}")
        
        # Optional parameters
        if "custom_query" in credentials:
            self.custom_query = str(credentials.get("custom_query"))
        
        if "include_comments" in credentials:
            self.include_comments = bool(credentials.get("include_comments"))
        
        # Log that credentials were loaded
        logger.info(f"CREDENTIALS LOADED: url={self.base_url}, token={'*****' + self.token[-4:] if self.token else None}")
        logger.info(f"PROJECT CONFIG: project_id={self.project_id if hasattr(self, 'project_id') else 'None'}, project_ids={self.project_ids if hasattr(self, 'project_ids') else 'None'}")

    def validate_connector_settings(self) -> None:
        """
        Validate connector settings by testing API connectivity.
        """
        # Critical validation - check for URL and token
        if not self.base_url:
            logger.error("CRITICAL ERROR: Missing YouTrack URL - check credentials!")
            raise ConnectorMissingCredentialError(
                "Missing required YouTrack URL in credentials"
            )
            
        if not self.token:
            logger.error("CRITICAL ERROR: Missing YouTrack token - check credentials!")
            raise ConnectorMissingCredentialError(
                "Missing required YouTrack token in credentials"
            )

        # Debug log of all settings
        logger.info("Validating connector settings with the following configuration:")
        if hasattr(self, "connector_specific_config") and self.connector_specific_config:
            logger.info(f"connector_specific_config: {self.connector_specific_config}")
        else:
            logger.info("No connector_specific_config present")
            
        # Collect all configured project IDs for validation
        project_ids = []
        
        # Check if we have a single project_id
        if hasattr(self, 'project_id') and self.project_id:
            project_ids.append(self.project_id)
            logger.info(f"Found project_id: {self.project_id}")
        
        # Check for project_ids in class properties or connector_specific_config
        if hasattr(self, 'project_ids'):
            if isinstance(self.project_ids, list):
                project_ids.extend(self.project_ids)
            elif isinstance(self.project_ids, str):
                parsed_ids = [pid.strip() for pid in self.project_ids.split(',') if pid.strip()]
                project_ids.extend(parsed_ids)
        
        # We need at least one project ID for validation
        if not project_ids:
            # Emergency fallback: Check if youtrack_project_ids exists in connector_specific_config
            if hasattr(self, 'connector_specific_config') and self.connector_specific_config:
                if 'youtrack_project_ids' in self.connector_specific_config:
                    project_ids_value = self.connector_specific_config.get('youtrack_project_ids')
                    logger.info(f"Using youtrack_project_ids directly from connector_specific_config: {project_ids_value}")
                    if isinstance(project_ids_value, str) and project_ids_value.strip():
                        # Directly use the first ID from the string for validation
                        project_id = project_ids_value.split(',')[0].strip()
                        if project_id:
                            project_ids.append(project_id)
                            # Also set as the project_id attribute for backward compatibility
                            self.project_id = project_id
                            logger.info(f"Emergency fallback: Using first ID from youtrack_project_ids: {project_id}")
            
            # Final check - if still no project IDs, raise error
            if not project_ids:
                logger.error("No project IDs found in connector settings")
                raise ConnectorMissingCredentialError(
                    "Missing project ID(s) in connector settings. Please configure at least one project ID in the YouTrack 'Project IDs' field."
                )
            
        # Use the first project ID for validation
        validation_project_id = project_ids[0]
        logger.info(f"Using project ID {validation_project_id} for validation (out of {len(project_ids)} configured projects)")
        
        # Log validation attempt with parameters for debugging
        logger.info(f"Validating YouTrack connector with:")
        logger.info(f"  base_url: {self.base_url}")
        logger.info(f"  validation project_id: {validation_project_id}")
        logger.info(f"  total configured projects: {len(project_ids)}")
        logger.info(f"  token present: {'Yes' if self.token else 'No'}")
        logger.info(f"  api_url: {self.api_url}")
        
        try:
            # Test API by trying to fetch one issue from the validation project
            url = f"{self.api_url}/issues"
            params = {
                "query": f"project: {validation_project_id}",
                "$top": 1
            }
            
            logger.info(f"Making validation request to: {url} with query: {params['query']}")
            response = requests.get(url, headers=self.headers, params=params)
            
            # Log the response for debugging
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    logger.info(f"Validation successful - got {len(data)} issues in response")
                    if len(data) > 0:
                        # Log details of first issue
                        logger.info(f"First issue: id={data[0].get('id')}, summary={data[0].get('summary')}")
                else:
                    logger.warning(f"Unexpected response format: {type(data)}")
            
            response.raise_for_status()
            logger.info(f"Successfully validated YouTrack connector for project {validation_project_id}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to validate YouTrack connector: {e}")
            logger.error(f"Response: {response.text if 'response' in locals() else 'No response'}")
            if 'response' in locals():
                logger.error(f"Status code: {response.status_code}")
            raise ConnectorMissingCredentialError(
                f"Could not connect to YouTrack API: {e}"
            )

    def _make_api_request(self, url: str, params: Optional[Dict[str, Any]] = None) -> Optional[List[Dict[str, Any]]]:
        """Makes a GET request to the YouTrack API with rate limit handling."""
        if not self.headers or "Authorization" not in self.headers:
            raise ConnectorMissingCredentialError("YouTrack credentials not loaded.")
        
        retries = 3
        for attempt in range(retries):
            try:
                response = requests.get(url, headers=self.headers, params=params)
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

    def list_available_projects(self) -> List[Dict[str, Any]]:
        """
        Lists all available YouTrack projects.
        Returns a list of project details that can be used for configuration.
        """
        if not self.api_url:
            raise ConnectorMissingCredentialError("YouTrack connector not properly configured (api_url missing).")
        
        try:
            # Fetch all projects
            url = f"{self.api_url}/admin/projects"
            projects = self._make_api_request(url)
            
            if not projects or not isinstance(projects, list):
                logger.error("Failed to fetch projects or unexpected response format")
                return []
            
            logger.info(f"Found {len(projects)} projects")
            return projects
            
        except Exception as e:
            logger.error(f"Error listing available projects: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def _fetch_issues_from_project(self, project_id: str, updated_since: Optional[datetime] = None) -> Iterator[List[dict]]:
        """
        Fetches issues from a specific project, handling pagination.
        Filters by 'updated_since' if provided.
        """
        if not self.api_url or not project_id:
            raise ConnectorMissingCredentialError("YouTrack connector not properly configured (api_url or project_id missing).")

        # Build query
        query = f"project: {project_id}"
        
        # Add custom query if provided
        if self.custom_query:
            query += f" {self.custom_query}"
        
        # Add updated_since filter if provided
        if updated_since:
            # Convert to YouTrack date format (YYYY-MM-DDThh:mm:ss)
            updated_since_str = updated_since.strftime("%Y-%m-%dT%H:%M:%S")
            query += f" updated: {updated_since_str} .."
        
        skip = 0
        top = 30  # Number of issues per batch
        
        while True:
            url = f"{self.api_url}/issues"
            params = {
                "query": query,
                "$skip": skip,
                "$top": top,
                "fields": "id,idReadable,summary,description,created,updated,customFields,reporter(name),tags(name),links(direction,linkType(name),issues(idReadable,summary))"
            }

            logger.info(f"Fetching issues from YouTrack project {project_id}, skip {skip}...")
            issue_batch = self._make_api_request(url, params)

            if issue_batch is None:  # Error occurred
                logger.error(f"Failed to fetch issues for project {project_id}, skip {skip}.")
                break
            
            if not isinstance(issue_batch, list):
                logger.error(f"Unexpected API response format for issues: {type(issue_batch)}. Expected list.")
                break

            if not issue_batch:  # No more issues
                logger.info(f"No more issues found for project {project_id} on skip {skip}.")
                break
            
            logger.info(f"Fetched {len(issue_batch)} issues from project {project_id}, skip {skip}.")
            
            # Fetch comments if needed
            if self.include_comments:
                for issue in issue_batch:
                    issue_id = issue.get("id")
                    if issue_id:
                        issue["comments"] = self._fetch_issue_comments(issue_id)
            
            yield issue_batch

            if len(issue_batch) < top:
                logger.info(f"Last batch reached for project {project_id}.")
                break
            
            skip += top
            time.sleep(1)  # Basic rate limiting

    def _fetch_issue_comments(self, issue_id: str) -> List[Dict[str, Any]]:
        """Fetch comments for a specific issue."""
        if not self.api_url or not issue_id:
            return []
        
        url = f"{self.api_url}/issues/{issue_id}/comments"
        params = {
            "fields": "id,text,created,updated,author(name)"
        }
        
        comments = self._make_api_request(url, params)
        if not comments or not isinstance(comments, list):
            return []
        
        logger.info(f"Fetched {len(comments)} comments for issue {issue_id}")
        return comments

    def _process_issues(self, project_ids: List[str], start_time: Optional[datetime] = None) -> GenerateDocumentsOutput:
        """
        Process issues from multiple projects, converting them to Onyx Documents.
        Accepts a list of project IDs to fetch from.
        """
        if not self.base_url:
            raise ConnectorMissingCredentialError("YouTrack URL not loaded.")
            
        logger.info("======= STARTING MULTI-PROJECT ISSUE PROCESSING =======")
        
        # Handle case where a single project ID string is passed
        if isinstance(project_ids, str):
            project_ids = [project_ids]
            
        # Make sure we have at least one project ID
        if not project_ids:
            logger.error("No project IDs provided for processing")
            raise ValueError("No project IDs provided for processing")
            
        logger.info(f"Processing issues from {len(project_ids)} projects: {project_ids}")
        
        issue_count = 0
        
        try:
            # First, create a document directly - this should always appear in the index 
            # no matter what is happening with the issue processing
            canary_doc = Document(
                id=_YOUTRACK_ID_PREFIX + "CANARY_TEST_DOC",
                sections=[TextSection(link="", text="This is a test document to verify indexing")],
                source=DocumentSource.YOUTRACK,  # This needs to be added to DocumentSource
                semantic_identifier="CANARY TEST DOCUMENT",
                metadata={"test": "canary"},
                doc_updated_at=datetime.now(timezone.utc),
            )
            
            # Yield the canary document by itself to ensure it gets indexed
            logger.info("====== YIELDING CANARY DOCUMENT ======")
            yield [canary_doc]
            logger.info("====== CANARY DOCUMENT YIELDED ======")
            
            # Debug - log project IDs being processed
            logger.info(f"ENHANCED DEBUG: Processing {len(project_ids)} projects: {project_ids}")
            
            # Process each project one by one
            for project_id in project_ids:
                logger.info(f"Processing project ID: {project_id}")
                project_issue_count = 0
                
                # Extra debugging to verify API calls
                logger.error(f"CRITICAL DEBUG: Making API call to fetch issues for project {project_id}")
                
                # Process issues in batches for this project
                batch_index = 0
                for issue_list_from_api in self._fetch_issues_from_project(project_id, start_time):
                    batch_index += 1
                    if not issue_list_from_api:
                        logger.error(f"Received empty issue batch #{batch_index} from project {project_id} - skipping")
                        continue
                    
                    logger.error(f"CRITICAL DEBUG: Processing batch #{batch_index} with {len(issue_list_from_api)} issues from project {project_id}")
                    logger.error(f"CRITICAL DEBUG: First issue ID in batch: {issue_list_from_api[0].get('id', 'UNKNOWN')}")
                    project_issue_count += len(issue_list_from_api)
                    issue_count += len(issue_list_from_api)
                    
                    # Process each batch of issues separately to avoid any cross-batch dependencies
                    current_batch = []
                    
                    for issue_data in issue_list_from_api:
                        try:
                            issue_id = str(issue_data.get('id', 'UNKNOWN'))
                            doc = _create_doc_from_issue(issue_data, self.base_url)
                            current_batch.append(doc)
                            logger.info(f"Added issue ID {issue_id} to current batch")
                        except Exception as e:
                            logger.error(f"Failed to create document: {e}")
                            # Don't even try to create an error document - just skip it
                    
                    # Yield this batch immediately
                    if current_batch:
                        logger.info(f"====== YIELDING BATCH OF {len(current_batch)} DOCUMENTS ======")
                        yield current_batch
                        logger.info(f"====== BATCH YIELDED SUCCESSFULLY ======")
                
                logger.info(f"Completed processing project {project_id} - {project_issue_count} issues indexed")
            
            # Create a final document to confirm we reached the end
            final_doc = Document(
                id=_YOUTRACK_ID_PREFIX + "FINAL_TEST_DOC",
                sections=[TextSection(link="", text=f"This is the final document. Processed {issue_count} issues from {len(project_ids)} projects.")],
                source=DocumentSource.YOUTRACK,  # This needs to be added to DocumentSource
                semantic_identifier="FINAL TEST DOCUMENT",
                metadata={"issue_count": issue_count, "project_count": len(project_ids)},
                doc_updated_at=datetime.now(timezone.utc),
            )
            
            # Yield the final document by itself
            logger.info("====== YIELDING FINAL DOCUMENT ======")
            yield [final_doc]
            logger.info("====== FINAL DOCUMENT YIELDED ======")
            
            logger.info(f"======= COMPLETED PROCESSING {issue_count} ISSUES FROM {len(project_ids)} PROJECTS =======")
            
        except Exception as e:
            logger.error(f"Critical error in issue processing: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
            # Try to yield an error document as a last resort
            error_doc = Document(
                id=_YOUTRACK_ID_PREFIX + "CRITICAL_ERROR",
                sections=[TextSection(link="", text=f"Critical error during processing: {e}")],
                source=DocumentSource.YOUTRACK,  # This needs to be added to DocumentSource
                semantic_identifier="CRITICAL ERROR DOCUMENT",
                metadata={"error": str(e)},
                doc_updated_at=datetime.now(timezone.utc),
            )
            
            logger.info("====== YIELDING ERROR DOCUMENT ======")
            yield [error_doc]
            logger.info("====== ERROR DOCUMENT YIELDED ======")

    def load_from_state(self) -> GenerateDocumentsOutput:
        """Loads all issues from the configured projects."""
        # Get project_ids from connector config
        project_ids = []
        
        # Check if we have a single project_id or multiple project_ids in the configuration
        if hasattr(self, 'project_id') and self.project_id:
            # Single project ID provided directly
            project_ids.append(self.project_id)
        
        # Check for project_ids in connector_specific_config and class attributes
        if hasattr(self, 'connector_specific_config') and self.connector_specific_config:
            # Check for youtrack_project_ids in connector_specific_config
            if 'youtrack_project_ids' in self.connector_specific_config:
                project_ids_value = self.connector_specific_config.get('youtrack_project_ids')
                if isinstance(project_ids_value, list):
                    project_ids.extend(project_ids_value)
                    logger.info(f"Using project_ids from connector_specific_config['youtrack_project_ids'] (list): {project_ids_value}")
                elif isinstance(project_ids_value, str):
                    parsed_ids = [pid.strip() for pid in project_ids_value.split(',') if pid.strip()]
                    project_ids.extend(parsed_ids)
                    logger.info(f"Using project_ids from connector_specific_config['youtrack_project_ids'] (string): parsed as {parsed_ids}")
        
        # Also check if project_ids was set as a class attribute
        if hasattr(self, 'project_ids'):
            if isinstance(self.project_ids, list):
                # Multiple project IDs provided as a list
                project_ids.extend(self.project_ids)
                logger.info(f"Using project_ids from self.project_ids (list): {self.project_ids}")
            elif isinstance(self.project_ids, str):
                # Multiple project IDs provided as a comma-separated string
                parsed_ids = [project_id.strip() for project_id in self.project_ids.split(',') if project_id.strip()]
                project_ids.extend(parsed_ids)
                logger.info(f"Using project_ids from self.project_ids (string): parsed as {parsed_ids}")
            
        if not project_ids:
            raise ConnectorMissingCredentialError("No YouTrack project ID(s) configured for load_from_state.")
            
        # Double check credentials before starting indexing
        if not self.base_url or not self.token:
            logger.error(f"CRITICAL ERROR: Missing credentials in load_from_state! base_url={self.base_url}, token_present={'Yes' if self.token else 'No'}")
            logger.error(f"API URL: {self.api_url}, Headers: {bool(self.headers)}")
            raise ConnectorMissingCredentialError("Missing required YouTrack credentials for indexing")
            
        logger.info(f"Loading all issues from {len(project_ids)} YouTrack projects: {project_ids}")
        logger.info(f"Using base URL: {self.base_url}")
        
        # Explicitly log that we're starting to yield documents
        logger.info(f"Starting to yield documents from YouTrack projects")
        
        # Add deep debugging to trace all steps
        logger.info("CRITICAL DEBUG: About to call _process_issues")
        logger.info(f"CRITICAL DEBUG: Project IDs to process: {project_ids}")
        
        # Wrap the iterator in a debug wrapper
        issue_generator = self._process_issues(project_ids)
        logger.info("CRITICAL DEBUG: Got generator from _process_issues")
        
        # Process batches one by one with explicit logging
        batch_count = 0
        for doc_batch in issue_generator:
            batch_count += 1
            logger.info(f"CRITICAL DEBUG: Processing document batch #{batch_count} with {len(doc_batch)} documents")
            yield doc_batch
            logger.info(f"CRITICAL DEBUG: Successfully yielded batch #{batch_count}")
        
        logger.info(f"CRITICAL DEBUG: Completed all batches ({batch_count} total)")

    def poll_source(self, start: SecondsSinceUnixEpoch, end: SecondsSinceUnixEpoch) -> GenerateDocumentsOutput:
        """
        Polls for issues updated within the given time range.
        """
        # Get project_ids from connector config using the same logic as load_from_state
        project_ids = []
        
        # Check if we have a single project_id or multiple project_ids in the configuration
        if hasattr(self, 'project_id') and self.project_id:
            # Single project ID provided directly
            project_ids.append(self.project_id)
        
        # Check for project_ids in connector_specific_config and class attributes
        if hasattr(self, 'connector_specific_config') and self.connector_specific_config:
            # Check for youtrack_project_ids in connector_specific_config
            if 'youtrack_project_ids' in self.connector_specific_config:
                project_ids_value = self.connector_specific_config.get('youtrack_project_ids')
                if isinstance(project_ids_value, list):
                    project_ids.extend(project_ids_value)
                    logger.info(f"Poll: Using project_ids from connector_specific_config['youtrack_project_ids'] (list): {project_ids_value}")
                elif isinstance(project_ids_value, str):
                    parsed_ids = [pid.strip() for pid in project_ids_value.split(',') if pid.strip()]
                    project_ids.extend(parsed_ids)
                    logger.info(f"Poll: Using project_ids from connector_specific_config['youtrack_project_ids'] (string): parsed as {parsed_ids}")
        
        # Also check if project_ids was set as a class attribute
        if hasattr(self, 'project_ids'):
            if isinstance(self.project_ids, list):
                # Multiple project IDs provided as a list
                project_ids.extend(self.project_ids)
                logger.info(f"Poll: Using project_ids from self.project_ids (list): {self.project_ids}")
            elif isinstance(self.project_ids, str):
                # Multiple project IDs provided as a comma-separated string
                parsed_ids = [project_id.strip() for project_id in self.project_ids.split(',') if project_id.strip()]
                project_ids.extend(parsed_ids)
                logger.info(f"Poll: Using project_ids from self.project_ids (string): parsed as {parsed_ids}")
            
        if not project_ids:
            raise ConnectorMissingCredentialError("No YouTrack project ID(s) configured for poll_source.")
            
        # Double check credentials before starting polling
        if not self.base_url or not self.token:
            logger.error(f"CRITICAL ERROR: Missing credentials in poll_source! base_url={self.base_url}, token_present={'Yes' if self.token else 'No'}")
            logger.error(f"API URL: {self.api_url}, Headers: {bool(self.headers)}")
            raise ConnectorMissingCredentialError("Missing required YouTrack credentials for polling")
        
        start_datetime = datetime.fromtimestamp(start, tz=timezone.utc)
        
        logger.info(f"Polling {len(project_ids)} YouTrack projects for updates since {start_datetime.isoformat()}")
        logger.info(f"Using base URL: {self.base_url}, projects: {project_ids}")
        yield from self._process_issues(project_ids, start_datetime)

    def _get_slim_documents_for_issue_batch(self, issues: List[Dict[str, Any]]) -> List[SlimDocument]:
        """Convert a batch of issues to SlimDocuments."""
        slim_docs = []
        for issue in issues:
            issue_id = issue.get("id")
            if issue_id:
                # All we need is the ID - no permissions data needed for this connector
                slim_docs.append(
                    SlimDocument(
                        id=_YOUTRACK_ID_PREFIX + str(issue_id),
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
        # Get project_ids using same logic as load_from_state and poll_source
        project_ids = []
        
        # Check if we have a single project_id or multiple project_ids in the configuration
        if hasattr(self, 'project_id') and self.project_id:
            # Single project ID provided directly
            project_ids.append(self.project_id)
        
        # Check for project_ids in connector_specific_config and class attributes
        if hasattr(self, 'connector_specific_config') and self.connector_specific_config:
            # Check for youtrack_project_ids in connector_specific_config
            if 'youtrack_project_ids' in self.connector_specific_config:
                project_ids_value = self.connector_specific_config.get('youtrack_project_ids')
                if isinstance(project_ids_value, list):
                    project_ids.extend(project_ids_value)
                elif isinstance(project_ids_value, str):
                    parsed_ids = [pid.strip() for pid in project_ids_value.split(',') if pid.strip()]
                    project_ids.extend(parsed_ids)
        
        # Also check if project_ids was set as a class attribute
        if hasattr(self, 'project_ids'):
            if isinstance(self.project_ids, list):
                project_ids.extend(self.project_ids)
            elif isinstance(self.project_ids, str):
                parsed_ids = [project_id.strip() for project_id in self.project_ids.split(',') if project_id.strip()]
                project_ids.extend(parsed_ids)
            
        if not project_ids:
            raise ConnectorMissingCredentialError("No YouTrack project ID(s) configured for slim document retrieval.")
        
        start_datetime = datetime.fromtimestamp(start, tz=timezone.utc) if start else None
        
        # Process each project
        for project_id in project_ids:
            logger.info(f"Retrieving slim documents from project {project_id}")
            
            slim_batch: List[SlimDocument] = []
            for issue_batch in self._fetch_issues_from_project(project_id, start_datetime):
                # Convert to slim documents
                new_slim_docs = self._get_slim_documents_for_issue_batch(issue_batch)
                slim_batch.extend(new_slim_docs)
                
                # Heartbeat callback if provided
                if callback:
                    callback.heartbeat()
                
                if len(slim_batch) >= self.batch_size:
                    logger.info(f"Yielding batch of {len(slim_batch)} slim documents from project {project_id}")
                    yield slim_batch
                    slim_batch = []
            
            if slim_batch:
                logger.info(f"Yielding final batch of {len(slim_batch)} slim documents from project {project_id}")
                yield slim_batch
        
        logger.info(f"Completed retrieval of slim documents from {len(project_ids)} projects")
