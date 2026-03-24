# KPI Backend Audit (Dashboard + Related Pages)

Date: 2026-02-06

## Scope and rule
- Rule applied: KPI values shown in UI come from backend API payloads only.
- UI hardcoded/statics removed for KPI cards.
- Tenant filter: all listed endpoints run through `get_db` tenant scope. Query-level tenant filtering is enforced by session criteria (`app/db.py`), with explicit tenant filters where needed.

## KPI Inventory

### Dashboard (role-based + generic)
- Page/API: `AdminDashboard.tsx` -> `GET /dashboard/admin/stats`
  - Users/system/activity/performance blocks.
  - Formula source: `app/routers/dashboard.py:get_admin_stats`.
  - Period: total lifetime + rolling 7 days (`week_start`) + runtime uptime.
  - Tenant filter: automatic session tenant scope.
- Page/API: `ManagerDashboard.tsx` -> `GET /dashboard/manager/stats`
  - Calls/SMS/emails/conversations/rdv/contacts conversion.
  - Formula source: `app/routers/dashboard.py:get_manager_stats`.
  - Period: today, last 7 days, and totals.
  - Tenant filter: automatic session tenant scope.
- Page/API: `AgentDashboard.tsx` -> `GET /dashboard/agent/stats`
  - Agent personal KPIs (rdv, active conversations, contacts).
  - Formula source: `app/routers/dashboard.py:get_agent_stats`.
  - Period: today, last 7 days, upcoming.
  - Tenant filter: automatic session tenant scope.
- Page/API: `ViewerDashboard.tsx` -> `GET /dashboard/viewer/stats`
  - Totals + weekly new entities.
  - Formula source: `app/routers/dashboard.py:get_viewer_stats`.
  - Period: totals + rolling 7 days.
  - Tenant filter: automatic session tenant scope.
- Page/API: `DashboardPage.tsx` (generic fallback) -> `/dashboard/overview`, `/dashboard/stats/*`, `/dashboard/metrics/*`, `/dashboard/trends`
  - Formula source: `app/routers/dashboard.py` + `app/services/metrics.py`.
  - Period: explicit query windows or rolling windows by endpoint.
  - Tenant filter: automatic session tenant scope.

### Contacts
- Page/API: `ContactsPage.tsx` -> `GET /school/persons/stats/overview`
  - KPI: `total`, `active`, `inactive`, `candidates`, `parents`, `students`, `new_7d`, `conversion_rate`.
  - Formula source: `app/routers/school_people.py:persons_stats`.
  - Period: total + rolling 7 days for `new_7d`.
  - Tenant filter: automatic session tenant scope.

### Rendez-vous
- Page/API: `RendezVousPage.tsx` -> `GET /school/appointments`
  - KPI: `total`, `status_counts`, `today_count`, `week_count`.
  - Formula source: `app/routers/school_people.py:list_school_appointments`.
  - Period: today and rolling 7 days (`week_start`) based on `start_at`.
  - Tenant filter: automatic session tenant scope.

### Calendar
- Page/API: `CalendarPage.tsx` -> `GET /calendar/stats`
  - KPI: `total_events`, `confirmed_count`, `pending_count`, `cancelled_count`, `attendance_rate`, `cancel_rate`, `today_count`, `week_count`, `total_participants`, `avg_duration`.
  - Formula source: `app/routers/calendar.py:get_calendar_stats`.
  - Period: today + next 7 days for forward schedule metrics.
  - Tenant filter: automatic session tenant scope.
  - Note: placeholder `satisfaction` removed; replaced by real `cancel_rate`.

### Conversations
- Page/API: `ConversationsPage.tsx` -> `GET /kb/conversations/stats`
  - KPI: `total`, `by_channel`, `response_rate`, `today_count`, `week_count`, `avg_wait_time`, `avg_duration`, `total_duration_seconds`, `recording_count`, `recording_rate`, `consent_count`, `satisfaction`, `resolution_rate`.
  - Formula source: `app/routers/knowledge_base.py:conversations_stats`.
  - Period: totals + today + rolling 7 days.
  - Tenant filter: automatic session tenant scope.

### Calls
- Page/API: `CallsPage.tsx` -> `GET /kb/conversations/stats?canal=call` + `GET /kb/conversations?canal=call`
  - KPI cards/metrics all from `stats` payload; list from paginated conversations.
  - Formula source: `app/routers/knowledge_base.py:conversations_stats`.
  - Period: totals + today + rolling 7 days.
  - Tenant filter: automatic session tenant scope.

### WhatsApp
- Page/API: `WhatsAppPage.tsx` -> `GET /notifications/whatsapp` + `GET /kb/conversations/stats?canal=whatsapp`
  - KPI from `/notifications/whatsapp.summary`: totals, inbound/outbound, today_count, conversation_count, responded_conversations, response_rate, delivery_rate.
  - KPI from `/kb/conversations/stats`: wait/duration/satisfaction/resolution.
  - Formula source: `app/routers/notifications.py:list_whatsapp_logs` + `app/routers/knowledge_base.py:conversations_stats`.
  - Period: totals + today + rolling 7 days (stats endpoint).
  - Tenant filter: explicit `tenant_uuid` filter in notifications endpoint + automatic scope.

### SMS
- Page/API: `SmsPage.tsx` -> `GET /notifications/sms`
  - KPI: `total`, `status_counts`, `today_count`, `kpis.delivery_rate`, `kpis.failure_rate`, `kpis.queued_count`, `kpis.unit_cost`, `kpis.cost_total`.
  - Formula source: `app/routers/notifications.py:list_sms_logs`.
  - Period: totals + today.
  - Tenant filter: explicit `tenant_uuid` filter + automatic scope.

### Emails
- Page/API: `EmailsPage.tsx` -> `GET /notifications/emails`
  - KPI: `total`, `status_counts`, `today_count`, `kpis.delivery_rate`, `kpis.failure_rate`, `kpis.queued_count`, `kpis.unit_cost`, `kpis.cost_total`.
  - Formula source: `app/routers/notifications.py:list_email_logs`.
  - Period: totals + today.
  - Tenant filter: explicit `tenant_uuid` filter + automatic scope.

### Notifications
- Page/API: `NotificationsPage.tsx` -> `GET /notifications/logs` + `GET /dashboard/notifications-series`
  - KPI built from backend series/log payloads only (no static counters): sent/failed totals, success/failure rates, pending count, today total, avg/day.
  - Formula source: `app/routers/dashboard.py:notifications_logs|metrics_notifications` + `app/services/metrics.py`.
  - Period: frontend requests rolling 7-day window (`time_min`, `time_max`).
  - Tenant filter: automatic session tenant scope.

## Test proof (seed -> expected KPI)
- `tests/test_kpi_backend.py`
  - `test_conversations_kpi_seed_expected`
  - `test_notifications_kpi_seed_expected`
  - `test_persons_kpi_seed_expected`
