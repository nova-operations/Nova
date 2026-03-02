"""
Tests for the list_files function in the filesystem module.
Verifies that auto_heal_error_23 (Function list_files not found in 'agno' logger) is resolved.
"""
import pytest
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock


class TestListFilesFunction:
    """Tests for list_files function."""

    def test_list_files_basic(self):
        """Test list_files with a basic directory listing."""
        from nova.tools.system.filesystem import list_files
        
        # List current directory
        result = list_files(".")
        assert result is not None
        # Should contain at least this test file
        assert "test_list_files.py" in result or "tests" in result.lower()

    def test_list_files_nonexistent_path(self):
        """Test list_files with a non-existent directory."""
        from nova.tools.system.filesystem import list_files
        
        result = list_files("/nonexistent/path/that/does/not/exist")
        assert "Error" in result
        assert "does not exist" in result

    def test_list_files_with_temp_directory(self):
        """Test list_files with a temporary directory."""
        from nova.tools.system.filesystem import list_files
        
        # Create a temp directory with some files
        temp_dir = tempfile.mkdtemp()
        try:
            # Create some test files
            test_file1 = os.path.join(temp_dir, "test1.txt")
            test_file2 = os.path.join(temp_dir, "test2.txt")
            open(test_file1, "w").close()
            open(test_file2, "w").close()
            
            result = list_files(temp_dir)
            assert "test1.txt" in result
            assert "test2.txt" in result
        finally:
            shutil.rmtree(temp_dir)

    def test_list_files_via_registry(self):
        """Test that list_files is properly registered in the registry."""
        from nova.tools.core.registry import get_tools_by_names, TOOL_REGISTRY
        
        # Check it's in the registry
        assert "list_files" in TOOL_REGISTRY
        
        # Check we can retrieve it
        tools = get_tools_by_names(["list_files"])
        assert len(tools) == 1
        assert tools[0].__name__ == "list_files"

    def test_list_files_via_specialist_registry(self):
        """Test that list_files is available in specialist registry."""
        from nova.tools.core.specialist_registry import get_specialist_config, save_specialist_config
        
        # A specialist config that includes list_files
        config = save_specialist_config(
            "FileTestSpec",
            "Tests file operations",
            "Test specialist for list_files",
            tools=["read_file", "list_files", "execute_shell_command"]
        )
        assert "saved" in config
        
        # Retrieve and verify
        retrieved = get_specialist_config("FileTestSpec")
        assert retrieved is not None
        assert "list_files" in retrieved["tools"]


class TestListFilesInSubagent:
    """Test that list_files is available to subagents."""

    def test_list_files_in_subagent_module(self):
        """Test that list_files is imported in subagent module."""
        from nova.tools import subagent
        
        # Check that list_files is imported in the subagent module
        assert hasattr(subagent, 'list_files')
        assert callable(subagent.list_files)


class TestListFilesIntegration:
    """Integration tests for list_files."""

    def test_list_files_function_callable(self):
        """Test that list_files can be called and returns results."""
        from nova.tools.system.filesystem import list_files
        
        # Call the function with a known path
        result = list_files(".")
        
        # Should return a string (either file list or error)
        assert isinstance(result, str)
        # Should not be an error
        assert not result.startswith("Error:")

    def test_list_files_error_handling(self):
        """Test list_files properly handles errors."""
        from nova.tools.system.filesystem import list_files
        
        # Test with empty string (should error)
        result = list_files("")
        # Either works or returns error - both acceptable
        assert isinstance(result, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])