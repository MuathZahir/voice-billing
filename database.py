import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.ext.declarative import declarative_base
from config import DATABASE_URL # Corrected import

# Use DATABASE_URL from config
engine = create_engine(DATABASE_URL) # Changed to use DATABASE_URL
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Create a scoped session for thread safety in Flask
db_session = scoped_session(SessionLocal)

def init_db():
    # Import all modules here that might define models so they
    # are registered properly on the metadata. Otherwise
    # you will have to import them first before calling init_db()
    import models
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("Database tables created (if they didn't exist).")

    # Seed initial branches if the Branch table is empty
    from models import Branch
    from config import KNOWN_BRANCHES
    session = db_session()
    try:
        if session.query(Branch).count() == 0:
            print("Seeding initial branches...")
            for branch_name in KNOWN_BRANCHES:
                branch = Branch(name=branch_name)
                session.add(branch)
            session.commit()
            print(f"Added {len(KNOWN_BRANCHES)} branches.")
        else:
            print("Branches table already populated.")
    except Exception as e:
        print(f"Error seeding branches: {e}")
        session.rollback()
    finally:
        session.close() # Use close() with scoped_session