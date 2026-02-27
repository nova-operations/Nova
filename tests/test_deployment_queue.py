"""
Tests for the deployment queue system.
"""

import unittest
import threading
import time
import os
import sys

# Set test environment
os.environ["DATABASE_URL"] = "sqlite:///test_deployment.db"

from nova.db.engine import get_db_engine
from nova.db.base import Base
from nova.db import deployment_models
from nova.queue_manager import QueueManager
from nova.task_tracker import TaskTracker
from nova.deployment_coordinator import DeploymentCoordinator
from nova.db.deployment_models import (
    DeploymentType, QueuePriority, QueueStatus, TaskStatus,
    DeploymentQueue, ActiveTask
)


class TestQueueManager(unittest.TestCase):
    """Tests for QueueManager."""
    
    @classmethod
    def setUpClass(cls):
        """Create test database."""
        engine = get_db_engine()
        Base.metadata.drop_all(engine)  # Clean start
        Base.metadata.create_all(engine)
        cls.qm = QueueManager()
    
    @classmethod
    def tearDownClass(cls):
        """Clean up test database."""
        if os.path.exists("test_deployment.db"):
            os.remove("test_deployment.db")
    
    def setUp(self):
        """Clean queue before each test."""
        session = self.qm._get_session()
        try:
            session.query(DeploymentQueue).delete()
            session.commit()
        finally:
            session.close()
    
    def test_add_to_queue_normal(self):
        """Test adding normal deployment to queue."""
        queue_id = self.qm.add_to_queue(
            deployment_type=DeploymentType.DEPLOY,
            target_service="test-service",
            requested_by="test-user",
            reason="Test deployment"
        )
        self.assertIsInstance(queue_id, int)
        self.assertGreater(queue_id, 0)
    
    def test_add_to_queue_destructive_high_priority(self):
        """Test that destructive actions get high priority."""
        queue_id = self.qm.add_to_queue(
            deployment_type=DeploymentType.REDEPLOY,
            target_service="test-service"
        )
        
        # Check priority was auto-upgraded
        session = self.qm._get_session()
        try:
            item = session.query(DeploymentQueue).filter(
                DeploymentQueue.id == queue_id
            ).first()
            self.assertEqual(item.priority, QueuePriority.HIGH)
        finally:
            session.close()
    
    def test_get_next_pending(self):
        """Test getting next pending item - highest priority first."""
        # Add LOW first, then CRITICAL (should come first)
        self.qm.add_to_queue(DeploymentType.DEPLOY, "service-low", priority=QueuePriority.LOW)
        self.qm.add_to_queue(DeploymentType.REDEPLOY, "service-critical", priority=QueuePriority.CRITICAL)
        
        next_item = self.qm.get_next_pending()
        self.assertIsNotNone(next_item)
        # Critical priority should come first
        self.assertEqual(next_item.target_service, "service-critical")
    
    def test_update_status(self):
        """Test updating queue item status."""
        queue_id = self.qm.add_to_queue(DeploymentType.DEPLOY, "test-service")
        
        success = self.qm.update_status(queue_id, QueueStatus.PROCESSING)
        self.assertTrue(success)
        
        # Verify status changed
        session = self.qm._get_session()
        try:
            item = session.query(DeploymentQueue).filter(
                DeploymentQueue.id == queue_id
            ).first()
            self.assertEqual(item.status, QueueStatus.PROCESSING)
            self.assertIsNotNone(item.started_at)
        finally:
            session.close()
    
    def test_cancel_queue_item(self):
        """Test cancelling a queue item."""
        queue_id = self.qm.add_to_queue(DeploymentType.DEPLOY, "test-service")
        
        success = self.qm.cancel_queue_item(queue_id)
        self.assertTrue(success)
        
        # Verify cancelled - should not appear in pending
        session = self.qm._get_session()
        try:
            pending = session.query(DeploymentQueue).filter(
                DeploymentQueue.status == QueueStatus.PENDING
            ).first()
            self.assertIsNone(pending)
        finally:
            session.close()


class TestTaskTracker(unittest.TestCase):
    """Tests for TaskTracker."""
    
    @classmethod
    def setUpClass(cls):
        """Create test database."""
        engine = get_db_engine()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        cls.tt = TaskTracker()
    
    def setUp(self):
        """Clean tasks before each test."""
        session = self.tt._get_session()
        try:
            session.query(ActiveTask).delete()
            session.query(deployment_models.TaskCheckpoint).delete()
            session.commit()
        finally:
            session.close()
    
    def test_register_task(self):
        """Test registering a new task."""
        success = self.tt.register_task(
            task_id="test-task-1",
            task_type="research",
            subagent_name="ResearchAgent",
            project_id="project-1"
        )
        self.assertTrue(success)
    
    def test_register_task_collision(self):
        """Test that duplicate task ID is rejected."""
        self.tt.register_task(
            task_id="test-task-2",
            task_type="research",
            subagent_name="ResearchAgent"
        )
        
        # Try to register same ID again
        success = self.tt.register_task(
            task_id="test-task-2",
            task_type="research",
            subagent_name="ResearchAgent"
        )
        self.assertFalse(success)
    
    def test_unregister_task(self):
        """Test completing a task."""
        self.tt.register_task(task_id="test-task-3", task_type="test", subagent_name="TestAgent")
        
        success = self.tt.unregister_task("test-task-3", final_state={"result": "done"})
        self.assertTrue(success)
        
        # Verify task is completed
        active = self.tt.get_active_tasks()
        task_ids = [t["task_id"] for t in active]
        self.assertNotIn("test-task-3", task_ids)
    
    def test_update_heartbeat(self):
        """Test heartbeat update."""
        self.tt.register_task(task_id="test-task-4", task_type="test", subagent_name="TestAgent")
        
        success = self.tt.update_heartbeat("test-task-4")
        self.assertTrue(success)
    
    def test_update_progress(self):
        """Test progress update."""
        self.tt.register_task(task_id="test-task-5", task_type="test", subagent_name="TestAgent")
        
        success = self.tt.update_progress("test-task-5", 50)
        self.assertTrue(success)
    
    def test_create_checkpoint(self):
        """Test creating task checkpoint."""
        self.tt.register_task(task_id="test-task-6", task_type="test", subagent_name="TestAgent")
        
        checkpoint_id = self.tt.create_checkpoint(
            task_id="test-task-6",
            state={"step": 5, "data": [1, 2, 3]},
            checkpoint_type="manual"
        )
        self.assertIsNotNone(checkpoint_id)
        self.assertIsInstance(checkpoint_id, int)
    
    def test_get_latest_checkpoint(self):
        """Test retrieving checkpoint."""
        self.tt.register_task(task_id="test-task-7", task_type="test", subagent_name="TestAgent")
        
        self.tt.create_checkpoint(task_id="test-task-7", state={"step": 1}, checkpoint_type="auto")
        time.sleep(0.1)
        self.tt.create_checkpoint(task_id="test-task-7", state={"step": 2}, checkpoint_type="auto")
        
        checkpoint = self.tt.get_latest_checkpoint("test-task-7")
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint["state"]["step"], 2)
    
    def test_pause_and_resume_task(self):
        """Test pausing and resuming a task."""
        self.tt.register_task(task_id="test-task-8", task_type="test", subagent_name="TestAgent",
                        initial_state={"value": 100})
        
        # Pause
        success = self.tt.pause_task("test-task-8")
        self.assertTrue(success)
        
        # Verify paused
        session = self.tt._get_session()
        try:
            task = session.query(ActiveTask).filter(
                ActiveTask.task_id == "test-task-8"
            ).first()
            self.assertEqual(task.status, TaskStatus.PAUSED)
        finally:
            session.close()
        
        # Resume
        success = self.tt.resume_task("test-task-8")
        self.assertTrue(success)
    
    def test_get_active_count(self):
        """Test getting active task count."""
        initial_count = self.tt.get_active_count()
        
        self.tt.register_task(task_id="test-task-9", task_type="test", subagent_name="TestAgent")
        
        self.assertEqual(self.tt.get_active_count(), initial_count + 1)


class TestDeploymentCoordinator(unittest.TestCase):
    """Tests for DeploymentCoordinator."""
    
    @classmethod
    def setUpClass(cls):
        """Create test database."""
        engine = get_db_engine()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        cls.dc = DeploymentCoordinator()
    
    def setUp(self):
        """Clean before each test."""
        session = self.dc._get_session()
        try:
            session.query(DeploymentQueue).delete()
            session.commit()
        finally:
            session.close()
    
    def test_initialization(self):
        """Test coordinator initialization."""
        self.assertIsNotNone(self.dc.queue_manager)
        self.assertIsNotNone(self.dc.task_tracker)
    
    def test_queue_deployment(self):
        """Test queueing deployment through coordinator."""
        queue_id = self.dc.queue_deployment(
            deployment_type="redeploy",
            target_service="test-service",
            requested_by="user-1"
        )
        
        self.assertIsInstance(queue_id, int)
    
    def test_get_queue_status(self):
        """Test getting queue status."""
        self.dc.queue_deployment("deploy", "service-1")
        self.dc.queue_deployment("restart", "service-2")
        
        status = self.dc.get_queue_status()
        self.assertIsInstance(status, list)
        self.assertEqual(len(status), 2)


def run_tests():
    """Run all tests."""
    # Clean up any existing test db
    if os.path.exists("test_deployment.db"):
        os.remove("test_deployment.db")
    
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    suite.addTests(loader.loadTestsFromTestCase(TestQueueManager))
    suite.addTests(loader.loadTestsFromTestCase(TestTaskTracker))
    suite.addTests(loader.loadTestsFromTestCase(TestDeploymentCoordinator))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Cleanup
    if os.path.exists("test_deployment.db"):
        os.remove("test_deployment.db")
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)