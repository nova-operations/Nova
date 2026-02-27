import pytest
from unittest.mock import MagicMock, patch
from nova.deployment_services import DeploymentService


@pytest.fixture
def clean_deployment_service():
    # DeploymentService is a singleton, reset it for testing
    DeploymentService._instance = None
    service = DeploymentService()
    return service


def test_deployment_service_singleton(clean_deployment_service):
    s2 = DeploymentService()
    assert clean_deployment_service is s2


def test_register_task(clean_deployment_service):
    mock_tt = MagicMock()
    clean_deployment_service._task_tracker = mock_tt

    clean_deployment_service.register_task("t1", "type", "agent")
    mock_tt.register_task.assert_called_once_with(
        task_id="t1",
        task_type="type",
        subagent_name="agent",
        project_id=None,
        description=None,
        initial_state=None,
    )


def test_queue_deployment(clean_deployment_service):
    mock_coord = MagicMock()
    clean_deployment_service._coordinator = mock_coord

    clean_deployment_service.queue_deployment("deploy", "service", "user")
    mock_coord.queue_deployment.assert_called_once_with(
        deployment_type="deploy",
        target_service="service",
        requested_by="user",
        reason=None,
    )
