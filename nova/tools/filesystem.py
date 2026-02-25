import os


def read_file(filepath: str) -> str:
    """Reads the content of a file."""
    try:
        with open(filepath, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(filepath: str, content: str) -> str:
    """Writes content to a file."""
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w") as f:
            f.write(content)
        return "File written successfully."
    except Exception as e:
        return f"Error writing file: {e}"


def list_files(path: str = ".") -> str:
    """Lists files in a directory."""
    try:
        return "\n".join(os.listdir(path))
    except Exception as e:
        return f"Error listing files: {e}"


def delete_file(filepath: str) -> str:
    """Deletes a file."""
    try:
        os.remove(filepath)
        return "File deleted successfully."
    except Exception as e:
        return f"Error deleting file: {e}"


def create_directory(path: str) -> str:
    """Creates a directory."""
    try:
        os.makedirs(path, exist_ok=True)
        return "Directory created successfully."
    except Exception as e:
        return f"Error creating directory: {e}"
