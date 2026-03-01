"""Tests for error_bus filter functionality."""
import logging
import pytest
from unittest.mock import patch, MagicMock
from nova.tools.error_bus import ErrorBusHandler


class TestErrorBusHandler:
    """Test the ErrorBusHandler filtering logic."""

    def test_filter_run_bash_command_not_found(self):
        """Verify 'Function run_bash_command not found' is filtered."""
        handler = ErrorBusHandler()
        record = MagicMock()
        record.name = "test_logger"
        record.levelno = logging.ERROR
        record.getMessage.return_value = "Function run_bash_command not found"
        record.exc_info = None
        record.exc_text = None

        # Should not log to DB (filtered)
        with patch("nova.tools.error_bus.get_session_factory") as mock_session:
            mock_db = MagicMock()
            mock_session.return_value.return_value = mock_db
            handler.emit(record)
            
            # Verify session was NOT called (filtered)
            mock_session.assert_not_called()

    def test_filter_bug_fixer_diagnose_and_fix_bug_not_found(self):
        """Verify 'Function bug-fixer:diagnose_and_fix_bug not found' is filtered."""
        handler = ErrorBusHandler()
        record = MagicMock()
        record.name = "test_logger"
        record.levelno = logging.ERROR
        record.getMessage.return_value = "Function bug-fixer:diagnose_and_fix_bug not found"
        record.exc_info = None
        record.exc_text = None

        with patch("nova.tools.error_bus.get_session_factory") as mock_session:
            mock_db = MagicMock()
            mock_session.return_value.return_value = mock_db
            handler.emit(record)
            mock_session.assert_not_called()

    def test_allow_other_errors(self):
        """Verify non-filtered errors are NOT filtered (should log to DB)."""
        handler = ErrorBusHandler()
        record = MagicMock()
        record.name = "test_logger"
        record.levelno = logging.ERROR
        record.getMessage.return_value = "Some other real error"
        record.exc_info = None
        record.exc_text = None
        record.formatted = "Some other real error"

        with patch("nova.tools.error_bus.get_session_factory") as mock_session:
            mock_db = MagicMock()
            mock_session.return_value.return_value = mock_db
            handler.emit(record)
            # Session should be called (not filtered)
            mock_session.assert_called_once()

    def test_all_hallucination_filters_present(self):
        """Verify all known hallucination strings are in the filter list."""
        handler = ErrorBusHandler()
        
        # Get the filter patterns from emit method source inspection
        expected_filters = [
            "Function RAG not found",
            "Function grep not found",
            "Function run_bash_command not found",
            "Function bug-fixer:diagnose_and_fix_bug not found",  # auto_heal_error_15
        ]
        
        # Test each filter is actually working
        for filter_str in expected_filters:
            record = MagicMock()
            record.name = "test_logger"
            record.levelno = logging.ERROR
            record.getMessage.return_value = filter_str
            record.exc_info = None
            record.exc_text = None

            with patch("nova.tools.error_bus.get_session_factory") as mock_session:
                mock_db = MagicMock()
                mock_session.return_value.return_value = mock_db
                handler.emit(record)
                mock_session.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])