# UAT Scenario - Live 4 Channels

## Preconditions
- `ENABLE_DEV_ENDPOINTS=false`
- Signed webhook secrets configured
- At least 2 tenants available in test/staging
- One test candidate contact created per tenant

## Scenario A - Chat
1. Send admission question from chat widget.
2. Expect coherent response (FR/EN/WO), intent logged, conversation persisted.
3. Verify `/kb/conversations/stats` increments for current tenant only.

## Scenario B - SMS
1. Send inbound SMS webhook (signed).
2. Expect reply + `sms_logs` row + conversation/messages persisted.
3. Verify no data visible from another tenant.

## Scenario C - WhatsApp
1. Send inbound Meta/Twilio WhatsApp webhook (signed).
2. Expect reply + message persisted + `/notifications/whatsapp` summary updated.
3. Verify spoofed tenant headers/payload are ignored.

## Scenario D - Voice
1. Trigger voice inbound/outbound flow with signed webhook.
2. Ensure WS token contains tenant scope and call stream persists conversation/messages in same tenant.
3. Verify recording metadata and call KPIs update.

## Expected Exit Criteria
- All 4 channels pass without cross-tenant leakage.
- Dashboard KPIs reflect backend-calculated values only.
- No unsigned webhook accepted.
- No sensitive endpoint accessible without explicit dev flag.
