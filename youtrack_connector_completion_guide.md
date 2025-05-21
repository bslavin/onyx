# YouTrack Connector Completion Guide

This guide outlines the remaining steps required to complete the YouTrack connector integration in Onyx.

## Overview

The YouTrack connector allows Onyx to index content from JetBrains YouTrack projects, with support for querying multiple projects simultaneously. While the core connector logic is mostly implemented in `backend/onyx/connectors/youtrack/connector.py`, several integration tasks remain to fully enable the connector within the Onyx system.

## Completion Tasks

### 1. Add YouTrack to DocumentSource Enum

In `backend/onyx/configs/constants.py`, add YouTrack to the DocumentSource enum:

```python
class DocumentSource(str, Enum):
    # ... existing sources ...
    YOUTRACK = "youtrack"  # Add this line
    # ... other sources ...
```

### 2. Add YouTrack to ValidSources Enum in Frontend

In `web/src/lib/types.ts`, add YouTrack to the ValidSources enum:

```typescript
export enum ValidSources {
    // ... existing sources ...
    YouTrack = "youtrack",
    // ... other sources ...
}
```

### 3. Add YouTrack Icon and Metadata

Create a YouTrack icon component and add it to the frontend source metadata in `web/src/lib/sources.ts`:

```typescript
import {
    // ... existing imports ...
    YouTrackIcon,  // Create this icon component
} from "@/components/icons/icons";

// ...

export const SOURCE_METADATA_MAP: SourceMap = {
    // ... existing sources ...
    youtrack: {
        icon: YouTrackIcon,
        displayName: "YouTrack",
        category: SourceCategory.ProjectManagement,
        docs: "https://docs.onyx.app/connectors/youtrack",
    },
    // ... other sources ...
};
```

### 4. Register YouTrack Connector in Factory

In `backend/onyx/connectors/factory.py`, add the YouTrack connector to the connector map:

```python
from onyx.connectors.youtrack.connector import YouTrackConnector

# ...

def identify_connector_class(
    source: DocumentSource,
    input_type: InputType | None = None,
) -> Type[BaseConnector]:
    connector_map = {
        # ... existing connectors ...
        DocumentSource.YOUTRACK: YouTrackConnector,
        # ... other connectors ...
    }
    # ... rest of function ...
```

### 5. Add Connector Configuration Form in Frontend

Create the connector configuration form in `web/src/lib/connectors/connectors.tsx`:

```typescript
// YouTrack connector config
export interface YouTrackConfig {
  youtrack_project_ids?: string;
  custom_query?: string;
  include_comments?: boolean;
}

// ... later in the same file ...

export const CONNECTOR_CONFIGS: Record<
  string,
  ConfigurationData<any>
> = {
  // ... existing connectors ...
  
  [ValidSources.YouTrack]: {
    serviceProvider: "YouTrack",
    displayName: "YouTrack",
    inputs: [
      {
        label: "Project IDs",
        name: "youtrack_project_ids",
        type: "text",
        helpText:
          "Comma-separated list of YouTrack project IDs to index (e.g., PROJECT-1,PROJECT-2)",
        required: true,
      },
      {
        label: "Custom Query",
        name: "custom_query",
        type: "text",
        helpText:
          "Optional YouTrack query to filter which issues are indexed (e.g., #Unresolved)",
        required: false,
      },
      {
        label: "Include Comments",
        name: "include_comments",
        type: "toggle",
        helpText: "Whether to include issue comments in the indexed content",
        required: false,
        defaultValue: true,
      },
    ],
    credentialInputs: [
      {
        label: "YouTrack URL",
        name: "youtrack_url",
        type: "text",
        helpText:
          "Your YouTrack instance URL (e.g., https://youtrack.example.com)",
        required: true,
        placeholder: "https://youtrack.example.com",
      },
      {
        label: "API Token",
        name: "youtrack_token",
        type: "password",
        helpText:
          "A permanent token with read access to your projects",
        required: true,
      },
    ],
  },
  
  // ... other connectors ...
};
```

### 6. Create Icon Component

Create a YouTrack icon component in `web/src/components/icons/icons.tsx`:

```typescript
export const YouTrackIcon: React.FC<{ size?: number, className?: string }> = ({
  size = 24,
  className,
}) => {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      {/* YouTrack logo SVG path here */}
      <path
        d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 15h-2v-6h2v6zm4 0h-2v-6h2v6zm1-9.5A1.5 1.5 0 0 1 14.5 9h-5A1.5 1.5 0 0 1 8 7.5V6h8v1.5z"
        fill="currentColor"
      />
    </svg>
  );
};
```

## Testing Steps

1. List available YouTrack projects:
   ```bash
   python list_youtrack_projects.py --url <your-youtrack-url> --token <your-token>
   ```
   
   You can also get more detailed project information using the YouTrack API directly:
   ```bash
   curl -s -H "Authorization: Bearer <your-token>" -H "Accept: application/json" "<your-youtrack-url>/api/admin/projects?fields=id,name,shortName" | jq
   ```

2. Test the connector with specific projects:
   ```bash
   python test_youtrack_multi_project.py --url <your-youtrack-url> --token <your-token> --projects <project-shortNames>
   ```
   
   For example:
   ```bash
   python test_youtrack_multi_project.py --url https://youtrack.example.com --token your-token --projects PROJECT1,PROJECT2
   ```
   
   **Note:** Use project shortNames (e.g., "MAILHOP") rather than IDs (e.g., "0-5") for better compatibility.

3. Configure the connector in the Onyx admin interface:
   - Navigate to the Connectors page
   - Add a new YouTrack connector
   - Enter the YouTrack URL and API token
   - Specify project shortNames (comma-separated for multiple projects)
   - Optionally add a custom query to filter issues

4. Perform a test indexing run and verify issues are properly indexed

## Additional Enhancements (Optional)

1. Add more comprehensive error handling in the connector for API failures
2. Improve document chunking strategies for larger YouTrack issues
3. Add support for issue attachments
4. Implement structured metadata extraction for better search filtering
5. Add support for indexing Agile boards and other YouTrack entities

## References

- [YouTrack REST API Documentation](https://www.jetbrains.com/help/youtrack/devportal/youtrack-rest-api.html)
- [YouTrack Authentication Documentation](https://www.jetbrains.com/help/youtrack/devportal/authentication-rest-api.html)
- [YouTrack Issue Fields Reference](https://www.jetbrains.com/help/youtrack/devportal/api-entity-IssueFields.html)
