from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func # Import func
from database import Base
import datetime

class Branch(Base):
    __tablename__ = "branches"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)

    # Relationships (optional but good practice)
    transfers_from = relationship("Transfer", foreign_keys="Transfer.source_branch_id", back_populates="source_branch")
    transfers_to = relationship("Transfer", foreign_keys="Transfer.destination_branch_id", back_populates="destination_branch")

    def __repr__(self):
        return f"<Branch(name='{self.name}')>"

class Transfer(Base):
    __tablename__ = "transfers"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False)
    source_branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    destination_branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    recorded_by_whatsapp_number = Column(String, nullable=True) # Store who recorded it
    original_message_text = Column(String, nullable=True) # Store the text for audit

    # Relationships
    source_branch = relationship("Branch", foreign_keys=[source_branch_id], back_populates="transfers_from")
    destination_branch = relationship("Branch", foreign_keys=[destination_branch_id], back_populates="transfers_to")

    def __repr__(self):
        return f"<Transfer(amount={self.amount} {self.currency}, from={self.source_branch_id}, to={self.destination_branch_id}, time={self.timestamp})>"