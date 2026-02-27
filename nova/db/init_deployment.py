"""
Database initialization for deployment queue system.

Creates all necessary tables for the queuing and deployment management system.
"""

import logging
from nova.db.engine import get_db_engine
from nova.db.base import Base
from nova.db import deployment_models

logger = logging.getLogger(__name__)


def init_deployment_db():
    """Initialize all deployment-related database tables."""
    engine = get_db_engine()
    
    logger.info("Creating deployment database tables...")
    
    # Create all tables
    Base.metadata.create_all(engine)
    
    logger.info("Deployment database tables created successfully")


def drop_deployment_db():
    """Drop all deployment-related database tables."""
    engine = get_db_engine()
    
    logger.info("Dropping deployment database tables...")
    
    Base.metadata.drop_all(engine)
    
    logger.info("Deployment database tables dropped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_deployment_db()
    print("Database initialized successfully!")