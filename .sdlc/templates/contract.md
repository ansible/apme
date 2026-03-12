# Contract: [Interface Name]

## Overview

Brief description of what this interface does and who uses it.

## Interface Type

CLI | REST API | Python API | Event

---

## CLI Interface

### Command: `apme [command]`

```
apme [command] [arguments] [options]
```

### Commands

#### `apme command-name`

**Purpose**: What this command does.

**Usage**:
```bash
apme command-name <required-arg> [optional-arg] [options]
```

**Arguments**:

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `required-arg` | path | Yes | Description |
| `optional-arg` | string | No | Description |

**Options**:

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--option-name` | `-o` | string | `default` | Description |
| `--flag` | `-f` | bool | false | Description |

**Examples**:
```bash
# Basic usage
apme command-name path/to/file

# With options
apme command-name path/to/file --option-name value

# Short form
apme command-name path/to/file -o value -f
```

**Exit Codes**:

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments |

**Output**:

Standard output format:
```
[Example output]
```

JSON output (`--format json`):
```json
{
  "field": "value"
}
```

---

## REST API Interface

### Endpoint: `METHOD /path`

**Purpose**: What this endpoint does.

**Authentication**: [Required/Optional] - [Type]

**Request**:

```http
METHOD /api/v1/resource HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "field": "value"
}
```

**Request Parameters**:

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `param` | path | string | Yes | Description |
| `query` | query | string | No | Description |
| `field` | body | object | Yes | Description |

**Response**:

**Success (200)**:
```json
{
  "status": "success",
  "data": {}
}
```

**Error (4xx/5xx)**:
```json
{
  "status": "error",
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message"
  }
}
```

---

## Python API Interface

### Class: `ClassName`

```python
from apme.module import ClassName

instance = ClassName(config)
result = instance.method(param)
```

### Constructor

```python
def __init__(
    self,
    config: Config,
    *,
    option: str = "default",
) -> None:
    """Initialize ClassName.

    Args:
        config: Configuration object.
        option: Optional setting.

    Raises:
        ConfigError: If config is invalid.
    """
```

### Methods

#### `method_name`

```python
def method_name(
    self,
    required_param: ParamType,
    *,
    optional_param: str | None = None,
) -> ReturnType:
    """Brief description.

    Args:
        required_param: Description.
        optional_param: Description.

    Returns:
        Description of return value.

    Raises:
        ErrorType: When this error occurs.
    """
```

**Example**:
```python
result = instance.method_name(
    param_value,
    optional_param="value",
)
```

---

## Data Models

### Model: `ModelName`

```python
@dataclass
class ModelName:
    """Description of what this model represents."""

    field_name: FieldType
    """Description of field."""

    optional_field: str | None = None
    """Description of optional field."""
```

**JSON Representation**:
```json
{
  "field_name": "value",
  "optional_field": null
}
```

---

## Error Codes

| Code | HTTP Status | Description | Resolution |
|------|-------------|-------------|------------|
| `ERR_001` | 400 | Invalid input | Check input format |
| `ERR_002` | 404 | Resource not found | Verify resource exists |
| `ERR_003` | 500 | Internal error | Retry or contact support |

---

## Versioning

- **Current Version**: v1
- **Compatibility**: [Policy]
- **Deprecation**: [Policy]

## Change History

| Version | Date | Changes |
|---------|------|---------|
| v1 | YYYY-MM-DD | Initial release |
