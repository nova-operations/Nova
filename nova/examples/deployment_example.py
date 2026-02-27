"""
Example: How to use the deployment queue system

This example demonstrates how to integrate the deployment queue
system into your application.
"""

import logging
import time
from nova.deployment_services import deployment_service
from nova.db.deployment_models import DeploymentType, QueuePriority

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def deployment_executor(queue_item):
    """
    This function executes the actual deployment.
    Replace with your actual deployment logic.
    
    Args:
        queue_item: DeploymentQueue object with deployment details
    
    Returns:
        bool: True if deployment succeeded, False otherwise
    """
    logger.info(f"Executing deployment: {queue_item.deployment_type.value} "
                f"for {queue_item.target_service}")
    
    # Your deployment logic here
    # Examples:
    # - Deploy to Railway: subprocess.run(["railway", "up", "--detach"])
    # - Restart container: docker-compose restart
    # - Deploy to Kubernetes: kubectl apply -f manifest.yaml
    
    # Simulate deployment time
    time.sleep(2)
    
    return True  # Return True on success


def notification_handler(user_id: str, message: str):
    """
    Send notification to user.
    Replace with your notification logic (Telegram, email, etc.)
    
    Args:
        user_id: User identifier
        message: Message to send
    """
    logger.info(f"NOTIFICATION to {user_id}: {message}")
    # Example: bot.send_message(chat_id=user_id, text=message)


def main():
    # Step 1: Initialize the service
    deployment_service.initialize(
        deployment_executor=deployment_executor,
        notification_handler=notification_handler,
    )
    
    # Step 2: Start the coordinator
    deployment_service.start()
    
    # Step 3: Register a task (subagent work)
    task_id = deployment_service.register_task(
        task_id="agent-001",
        task_type="research",
        subagent_name="ResearchAgent",
        project_id="project-alpha",
        description="Gathering market data",
        initial_state={"step": 1, "data": []}
    )
    
    # Simulate task progress
    for i in range(10):
        deployment_service.update_task_progress("agent-001", (i + 1) * 10)
        deployment_service.update_task_heartbeat("agent-001")
        time.sleep(0.5)
    
    # Create checkpoint
    deployment_service.create_task_checkpoint(
        task_id="agent-001",
        state={"step": 5, "data": ["item1", "item2"]},
        checkpoint_type="auto"
    )
    
    # Step 4: Queue a deployment
    queue_id = deployment_service.queue_deployment(
        deployment_type="redeploy",  # This triggers high-priority queue
        target_service="project-alpha",
        requested_by="user-123",
        reason="Update to latest version"
    )
    
    logger.info(f"Queued deployment with ID: {queue_id}")
    
    # Step 5: Check system status
    status = deployment_service.get_system_status()
    logger.info(f"System status: {status}")
    
    # Wait for deployment to process
    time.sleep(5)
    
    # Step 6: Complete the task
    deployment_service.complete_task(
        task_id="agent-001",
        final_state={"step": 10, "data": ["result1", "result2"]}
    )
    
    # Check final status
    logger.info(f"Final queue status: {deployment_service.get_queue_status()}")
    
    # Step 7: Register a scheduled job
    deployment_service.register_scheduled_job(
        job_id="daily-backup",
        job_name="Daily Backup",
        cron_expression="0 2 * * *",  # 2 AM daily
        auto_resume=True
    )
    
    # Stop the coordinator when done
    deployment_service.stop()
    
    logger.info("Example completed!")


if __name__ == "__main__":
    main()