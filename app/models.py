import uuid
from sqlalchemy import (
    Column,
    String,
    DateTime,
    Boolean,
    LargeBinary,
    Text,
    ForeignKey,
    Numeric,
    BigInteger,
    Uuid as UUID,
    UniqueConstraint,
)
from sqlalchemy.sql import func
from .db import Base

DEFAULT_TENANT_UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")

class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actor = Column(String(100), nullable=False)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(100), nullable=True)
    details = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)

class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    email = Column(String(320), nullable=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    attempted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    success = Column(Boolean, default=False, nullable=False)
    failure_reason = Column(String(100), nullable=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    slug = Column(String(80), unique=True, nullable=False)
    name = Column(String(120), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("billing_plans.id", ondelete="SET NULL"), nullable=True)
    stripe_customer_id = Column(String(120), nullable=True)
    stripe_subscription_id = Column(String(120), nullable=True)
    subscription_status = Column(String(30), default="active", nullable=False)  # active, past_due, cancelled, trialing


class TenantChannel(Base):
    __tablename__ = "tenant_channels"
    __table_args__ = (
        UniqueConstraint("provider", "provider_key", name="uq_tenant_channels_provider_key"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    provider = Column(String(80), nullable=False)  # ex: twilio_sms, twilio_whatsapp, meta_whatsapp, email_inbound
    provider_key = Column(String(255), nullable=False)  # clé publique transmise par webhook/app public
    token_hash = Column(String(128), nullable=False)  # SHA-256(hex) d'un secret partagé (empty for widget_embed)
    is_active = Column(Boolean, default=True, nullable=False)
    allowed_origins = Column(Text, nullable=True)  # comma-separated origins for widget embed CORS validation


class TenantSettings(Base):
    __tablename__ = "tenant_settings"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    default_language = Column(String(10), default="fr", nullable=False)
    enabled_channels = Column(String(200), default="chat,email,sms,whatsapp,call", nullable=False)
    monthly_rdv_limit = Column(BigInteger, default=500, nullable=False)
    monthly_message_limit = Column(BigInteger, default=5000, nullable=False)
    monthly_call_limit = Column(BigInteger, default=2000, nullable=False)


class TenantQuotaUsage(Base):
    __tablename__ = "tenant_quota_usage"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    metric = Column(String(40), nullable=False)  # rendezvous, messages, calls
    period = Column(String(20), nullable=False)  # YYYY-MM
    used_count = Column(BigInteger, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    available_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    event_type = Column(String(120), nullable=False)
    aggregate_type = Column(String(80), nullable=False)
    aggregate_id = Column(String(120), nullable=False)
    payload = Column(Text, nullable=False)
    status = Column(String(20), default="pending", nullable=False)  # pending, sent, failed
    attempts = Column(BigInteger, default=0, nullable=False)
    last_error = Column(Text, nullable=True)


# ----------------------------------------------------------------
# Billing Plans (DB-driven, not hardcoded)
# ----------------------------------------------------------------

class BillingPlan(Base):
    __tablename__ = "billing_plans"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    slug = Column(String(40), unique=True, nullable=False)  # free, starter, pro, enterprise
    name = Column(String(80), nullable=False)
    description = Column(Text, nullable=True)
    monthly_price_cents = Column(BigInteger, default=0, nullable=False)  # Price in cents (USD)
    currency = Column(String(3), default="usd", nullable=False)
    monthly_message_limit = Column(BigInteger, default=100, nullable=False)
    monthly_call_limit = Column(BigInteger, default=10, nullable=False)
    monthly_rdv_limit = Column(BigInteger, default=20, nullable=False)
    monthly_ai_token_limit = Column(BigInteger, default=100000, nullable=False)
    enabled_channels = Column(String(200), default="chat", nullable=False)  # comma-separated
    features = Column(Text, nullable=True)  # JSON array of feature slugs
    stripe_price_id = Column(String(120), nullable=True)  # Stripe Price ID for checkout
    is_active = Column(Boolean, default=True, nullable=False)
    sort_order = Column(BigInteger, default=0, nullable=False)


class BillingInvoice(Base):
    __tablename__ = "billing_invoices"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    amount_cents = Column(BigInteger, default=0, nullable=False)
    currency = Column(String(3), default="usd", nullable=False)
    status = Column(String(20), default="draft", nullable=False)  # draft, paid, failed, void
    stripe_invoice_id = Column(String(120), nullable=True)
    stripe_payment_intent_id = Column(String(120), nullable=True)
    details = Column(Text, nullable=True)  # JSON with usage breakdown


# ----------------------------------------------------------------
# LLM Usage Tracking
# ----------------------------------------------------------------

class LLMUsageLog(Base):
    __tablename__ = "llm_usage_logs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    model = Column(String(60), nullable=False)
    call_type = Column(String(30), nullable=False)  # generate, extract, rephrase, embed, tts, stt
    channel = Column(String(20), nullable=True)
    prompt_tokens = Column(BigInteger, default=0, nullable=False)
    completion_tokens = Column(BigInteger, default=0, nullable=False)
    total_tokens = Column(BigInteger, default=0, nullable=False)
    cost_usd = Column(Numeric(12, 6), default=0, nullable=False)
    latency_ms = Column(BigInteger, default=0, nullable=False)
    conversation_id = Column(UUID(as_uuid=True), nullable=True)


# ----------------------------------------------------------------
# Media Attachments (WhatsApp / Email incoming files)
# ----------------------------------------------------------------

class MediaAttachment(Base):
    __tablename__ = "media_attachments"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    person_id = Column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"), nullable=True)
    channel = Column(String(20), nullable=False)  # whatsapp, email, chat
    direction = Column(String(10), nullable=False)  # inbound, outbound
    original_filename = Column(String(500), nullable=True)
    content_type = Column(String(120), nullable=False)  # MIME type
    file_size_bytes = Column(BigInteger, default=0, nullable=False)
    storage_path = Column(String(500), nullable=False)  # Local path or S3 key
    storage_backend = Column(String(20), default="local", nullable=False)  # local, s3
    source_url = Column(String(2000), nullable=True)  # Original URL (Twilio, Meta, etc.)
    metadata_json = Column(Text, nullable=True)  # JSON with extra info


# Knowledge base models (Sprint 1)

class Agent(Base):
    __tablename__ = "agents"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    specialite = Column(String(100), nullable=True)
    disponible = Column(Boolean, default=True, nullable=False)
    max_rdv_par_jour = Column(BigInteger, default=8, nullable=False)
    secteur_geographique = Column(String(500), nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    person_id = Column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"), nullable=True)
    resume = Column(Text, nullable=True)
    canal = Column(String(20), nullable=True)  # call, chat
    intention = Column(String(100), nullable=True)
    # Etat conversationnel persistant du noyau agent (JSON sérialisé)
    conversation_state = Column(Text, nullable=True)
    # Champs pour l'enregistrement des appels vocaux
    call_sid = Column(String(100), nullable=True)  # Twilio Call SID
    recording_sid = Column(String(100), nullable=True)  # Twilio Recording SID
    recording_url = Column(String(500), nullable=True)  # URL de l'enregistrement audio
    recording_duration = Column(BigInteger, nullable=True)  # Durée en secondes
    recording_consent = Column(Boolean, default=False, nullable=False)  # Consentement RGPD
    # Champs pour l'intervention manuelle
    status = Column(String(20), default="active", nullable=False)  # active, closed, pending_review
    assigned_to = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)  # Agent assigné
    mode = Column(String(20), default="auto", nullable=False)  # auto, manual, hybrid
    requires_validation = Column(Boolean, default=False, nullable=False)  # Nécessite validation avant envoi
    last_human_interaction = Column(DateTime(timezone=True), nullable=True)  # Dernière intervention humaine
    # Analyse de sentiment
    sentiment_score = Column(Numeric(scale=2), nullable=True)  # Score -1.0 à 1.0
    sentiment_label = Column(String(20), nullable=True)  # positive, negative, neutral
    sentiment_analyzed_at = Column(DateTime(timezone=True), nullable=True)


class RendezVous(Base):
    __tablename__ = "rendezvous"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    person_id = Column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"), nullable=True)
    track_id = Column(UUID(as_uuid=True), ForeignKey("school_tracks.id", ondelete="SET NULL"), nullable=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    start_at = Column(DateTime(timezone=True), nullable=False)
    end_at = Column(DateTime(timezone=True), nullable=False)
    agent = Column(String(100), nullable=True)  # legacy display cache, do not use as source of truth
    statut = Column(String(20), default="created", nullable=False)
    event_id = Column(String(128), nullable=True)  # Google Calendar event id


class Message(Base):
    __tablename__ = "messages"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)  # user, assistant, system
    canal = Column(String(20), nullable=True)
    content = Column(Text, nullable=False)


class EmailLog(Base):
    __tablename__ = "emails_logs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    person_id = Column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"), nullable=True)
    sujet = Column(String(200), nullable=True)
    statut = Column(String(20), default="pending", nullable=False)
    dedupe_key = Column(String(160), nullable=True)
    recipient = Column(String(320), nullable=True)
    provider_name = Column(String(80), nullable=True)
    provider_id = Column(String(100), nullable=True)
    direction = Column(String(20), default="outbound", nullable=False)
    last_error = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)


class SMSLog(Base):
    __tablename__ = "sms_logs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    person_id = Column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"), nullable=True)
    contenu = Column(Text, nullable=True)
    statut = Column(String(20), default="pending", nullable=False)
    dedupe_key = Column(String(160), nullable=True)
    recipient = Column(String(80), nullable=True)
    provider_name = Column(String(80), nullable=True)
    provider_id = Column(String(100), nullable=True)
    direction = Column(String(20), default="outbound", nullable=False)
    last_error = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)


# Internal Calendar models (Sprint 2)

class Calendar(Base):
    __tablename__ = "calendars"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    name = Column(String(120), nullable=False)
    owner = Column(String(120), nullable=True)
    timezone = Column(String(64), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)


class Event(Base):
    __tablename__ = "events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    calendar_id = Column(UUID(as_uuid=True), ForeignKey("calendars.id", ondelete="CASCADE"), nullable=False)
    rendezvous_id = Column(UUID(as_uuid=True), ForeignKey("rendezvous.id", ondelete="CASCADE"), nullable=True)
    title = Column(String(200), nullable=False)
    start_at = Column(DateTime(timezone=True), nullable=False)
    end_at = Column(DateTime(timezone=True), nullable=False)
    resource_key = Column(String(120), nullable=True)  # ex: agent email/id to scope conflicts
    attendees = Column(Text, nullable=True)  # comma-separated emails/phones
    description = Column(Text, nullable=True)
    status = Column(String(20), default="confirmed", nullable=False)


class EmailTemplate(Base):
    __tablename__ = "email_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_email_templates_tenant_name"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    name = Column(String(120), nullable=False)
    subject_template = Column(Text, nullable=False)
    html_template = Column(Text, nullable=False)
    text_template = Column(Text, nullable=True)


class Document(Base):
    __tablename__ = "documents"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(Text, nullable=True)


class SchoolDepartment(Base):
    __tablename__ = "school_departments"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_school_departments_tenant_name"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    name = Column(String(120), nullable=False)
    code = Column(String(40), nullable=True)
    description = Column(Text, nullable=True)


class SchoolProgram(Base):
    __tablename__ = "school_programs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    department_id = Column(UUID(as_uuid=True), ForeignKey("school_departments.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    delivery_mode = Column(String(30), default="onsite", nullable=False)
    access_level = Column(String(120), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)


class SchoolTrack(Base):
    __tablename__ = "school_tracks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    program_id = Column(UUID(as_uuid=True), ForeignKey("school_programs.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    annual_fee = Column(Numeric(scale=2), nullable=False)
    registration_fee = Column(Numeric(scale=2), nullable=False)
    monthly_fee = Column(Numeric(scale=2), nullable=False)
    certifications = Column(Text, nullable=True)
    options = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)


class SchoolAdmissionRequirement(Base):
    __tablename__ = "school_admission_requirements"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_school_admission_requirements_tenant_code"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    code = Column(String(80), nullable=False)
    title_fr = Column(String(255), nullable=False)
    title_en = Column(String(255), nullable=True)
    title_wo = Column(String(255), nullable=True)
    details_fr = Column(Text, nullable=True)
    details_en = Column(Text, nullable=True)
    details_wo = Column(Text, nullable=True)
    sort_order = Column(BigInteger, default=0, nullable=False)
    is_required = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class SchoolAdmissionPolicy(Base):
    __tablename__ = "school_admission_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_school_admission_policies_tenant_code"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    code = Column(String(80), nullable=False)
    text_fr = Column(Text, nullable=False)
    text_en = Column(Text, nullable=True)
    text_wo = Column(Text, nullable=True)
    sort_order = Column(BigInteger, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class Person(Base):
    __tablename__ = "persons"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    first_name = Column(String(120), nullable=False)
    last_name = Column(String(120), nullable=True)
    email = Column(String(320), nullable=True)
    phone = Column(String(40), nullable=True)
    preferred_language = Column(String(10), nullable=True)
    status = Column(String(30), default="active", nullable=False)
    notes = Column(Text, nullable=True)


class PersonRole(Base):
    __tablename__ = "person_roles"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    person_id = Column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(30), nullable=False)  # candidate, parent, student
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ParentStudentLink(Base):
    __tablename__ = "parent_student_links"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False)
    student_id = Column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False)
    relation = Column(String(50), nullable=True)  # pere, mere, tuteur
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# Auth models

class Role(Base):
    __tablename__ = "roles"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(64), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Permission(Base):
    __tablename__ = "permissions"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id = Column(BigInteger, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id = Column(BigInteger, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),)
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    first_name = Column(String(120), nullable=True)
    last_name = Column(String(120), nullable=True)
    phone = Column(String(40), nullable=True)
    email = Column(String(320), nullable=False)
    password_hash = Column(String(512), nullable=False)
    role_id = Column(BigInteger, ForeignKey("roles.id", ondelete="SET NULL"), nullable=True)
    mfa_secret = Column(String(256), nullable=True)
    mfa_enabled = Column(Boolean, default=False, nullable=False)
    token_version = Column(BigInteger, default=0, nullable=False)
    avatar_url = Column(String(500), nullable=True)
    last_login = Column(DateTime(timezone=True), nullable=True)


class Manager(Base):
    __tablename__ = "managers"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)


class Viewer(Base):
    __tablename__ = "viewers"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, default=DEFAULT_TENANT_UUID)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
