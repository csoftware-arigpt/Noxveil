import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        Index("ix_agents_last_seen", "last_seen"),
        Index("ix_agents_is_alive", "is_alive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    os_info: Mapped[str] = mapped_column(String(255), nullable=False)
    internal_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    external_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_alive: Mapped[bool] = mapped_column(Boolean, default=True)
    callback_interval: Mapped[int] = mapped_column(Integer, default=5)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="agent", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "hostname": self.hostname,
            "username": self.username,
            "os_info": self.os_info,
            "internal_ip": self.internal_ip,
            "external_ip": self.external_ip,
            "pid": self.pid,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "is_alive": self.is_alive,
            "callback_interval": self.callback_interval,
            "note": self.note,
        }


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_agent_status_created", "agent_id", "status", "created_at"),
        Index("ix_tasks_sent_at", "sent_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    agent: Mapped["Agent"] = relationship("Agent", back_populates="tasks")
    results: Mapped[list["Result"]] = relationship("Result", back_populates="task", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "command": self.command,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class Result(Base):
    __tablename__ = "results"
    __table_args__ = (Index("ix_results_task_received", "task_id", "received_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    output: Mapped[str] = mapped_column(Text, nullable=False)
    is_error: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped["Task"] = relationship("Task", back_populates="results")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "output": self.output,
            "is_error": self.is_error,
            "received_at": self.received_at.isoformat() if self.received_at else None,
        }


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    mfa_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "is_admin": self.is_admin,
            "mfa_enabled": self.mfa_enabled,
            "locked_until": self.locked_until.isoformat() if self.locked_until else None,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
        }


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_actor_id", "actor_id"),
        Index("ix_audit_logs_event_type", "event_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    actor_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    actor_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "actor_id": self.actor_id,
            "actor_username": self.actor_username,
            "event_type": self.event_type,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "ip_address": self.ip_address,
            "details": self.details,
        }
