/**
 * AELIXORIATech Chatbot Widget v2.0 - AVEC APPEL VOCAL
 * Widget de chatbot IA intégrable sur n'importe quel site web
 * Inclut la fonctionnalité d'appel vocal via Twilio Voice SDK
 *
 * UTILISATION :
 * <script src="https://sdk.twilio.com/js/client/releases/1.14.0/twilio.min.js"></script>
 * <script src="chatbot-widget-voice.js"></script>
 *
 * CONFIGURATION :
 * Modifiez les valeurs dans l'objet CONFIG ci-dessous
 */
(function() {
  'use strict';

  // ========================================
  // CONFIGURATION - MODIFIEZ ICI
  // ========================================
  const DEFAULT_CONFIG = {
    // URL de votre API backend AELIXORIATech
    apiUrl: typeof window !== 'undefined' && !['localhost', '127.0.0.1', '0.0.0.0'].includes(window.location.hostname) ? window.location.origin : 'http://localhost:8000',

    // Palette du chatbot
    primaryColor: '#080a27',
    headerColor: '#080a27',
    userBubbleColor: '#8f4f1c',
    userBubbleColorAlt: '#bd7539',
    assistantBubbleColor: '#fffdf8',
    widgetBackground: '#fbf7ef',
    messagesBackground: '#f0e8db',

    // Position : 'bottom-right' ou 'bottom-left'
    position: 'bottom-right',

    // Message de bienvenue
    welcomeMessage: "Bonjour ! Je suis Salma, votre assistante admission. Comment puis-je vous aider aujourd'hui ? 😊",

    // Informations de l'agent
    agentName: 'Salma Benali',
    agentTitle: 'Conseillère Admissions',
    agentAvatar: '👩🏾‍💼',
    // Photo reelle (modifiable depuis la page hote)
    agentAvatarUrl: 'https://images.unsplash.com/photo-1544005313-94ddf0286df2?auto=format&fit=crop&w=240&q=80',

    // Textes personnalisables
    inputPlaceholder: 'Écrivez votre message...',
    poweredByText: 'Propulsé par AELIXORIATech IA',

    // Options
    enableNotificationBadge: true,
    autoOpenDelay: 0, // 0 = pas d'ouverture auto, sinon délai en ms
    enableVoiceCall: true, // Activer l'appel vocal
    // Token public optionnel pour /voice/token (recommandé en prod)
    widgetToken: '',
    // NEW: Secure embed key (recommended for SaaS) — public, safe in HTML
    embedKey: '',
    // LEGACY: Credentials fail-closed multi-tenant (deprecated, use embedKey instead)
    providerKey: '',
    tenantToken: '',
  };
  // Permet la surcharge depuis la page hote:
  // window.AELIXORIATechWidgetVoiceConfig = { apiUrl, widgetToken, providerKey, tenantToken, ... }
  // Compatibilite legacy conservee avec window.AelixoriaWidgetVoiceConfig / window.AelixoriaWidgetConfig.
  const runtimeConfig =
    typeof window !== 'undefined' &&
    typeof window.AELIXORIATechWidgetVoiceConfig === 'object'
      ? window.AELIXORIATechWidgetVoiceConfig
      : typeof window !== 'undefined' && typeof window.AELIXORIATechWidgetConfig === 'object'
        ? window.AELIXORIATechWidgetConfig
        : typeof window !== 'undefined' && typeof window.AelixoriaWidgetVoiceConfig === 'object'
          ? window.AelixoriaWidgetVoiceConfig
          : typeof window !== 'undefined' && typeof window.AelixoriaWidgetConfig === 'object'
            ? window.AelixoriaWidgetConfig
            : {};
  const CONFIG = { ...DEFAULT_CONFIG, ...runtimeConfig };

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function escapeAttr(value) {
    return escapeHtml(value);
  }

  function getAgentAvatarMarkup() {
    const avatarUrl = typeof CONFIG.agentAvatarUrl === 'string' ? CONFIG.agentAvatarUrl.trim() : '';
    if (avatarUrl) {
      return `<img class="aelixoria-chatbot-avatar-image" src="${escapeAttr(avatarUrl)}" alt="${escapeAttr(CONFIG.agentName)}" loading="lazy" referrerpolicy="no-referrer" />`;
    }
    return `<span class="aelixoria-chatbot-avatar-fallback">${escapeHtml(CONFIG.agentAvatar || '👩🏾‍💼')}</span>`;
  }

  // ========================================
  // STYLES CSS
  // ========================================
  const posRight = CONFIG.position === 'bottom-right';
  const styles = `
    @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');

    .aelixoria-chatbot-button {
      position: fixed;
      ${posRight ? 'right: 24px;' : 'left: 24px;'}
      bottom: 24px;
      z-index: 9999;
      background:
        radial-gradient(circle at 20% 15%, rgba(255, 255, 255, 0.3), transparent 45%),
        linear-gradient(150deg, ${CONFIG.primaryColor} 0%, #0f173f 100%);
      color: white;
      border-radius: 50%;
      width: 64px;
      height: 64px;
      border: none;
      cursor: pointer;
      box-shadow: 0 16px 34px rgba(8, 10, 39, 0.35);
      transition: all 0.3s ease;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .aelixoria-chatbot-button:hover {
      transform: scale(1.1);
      box-shadow: 0 20px 38px rgba(8, 10, 39, 0.42);
    }
    .aelixoria-chatbot-badge {
      position: absolute;
      top: -5px;
      right: -5px;
      background-color: #d1432d;
      color: white;
      border-radius: 50%;
      width: 24px;
      height: 24px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 12px;
      font-weight: bold;
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.5; }
    }
    .aelixoria-chatbot-window {
      position: fixed;
      ${posRight ? 'right: 24px;' : 'left: 24px;'}
      bottom: 24px;
      z-index: 9999;
      width: 408px;
      height: 650px;
      background: ${CONFIG.widgetBackground};
      border: 1px solid rgba(8, 10, 39, 0.12);
      border-radius: 22px;
      box-shadow: 0 30px 80px rgba(8, 10, 39, 0.3);
      display: none;
      flex-direction: column;
      overflow: hidden;
      font-family: 'Manrope', 'Space Grotesk', 'Avenir Next', sans-serif;
    }
    .aelixoria-chatbot-window.open {
      display: flex;
      animation: slideUp 0.32s ease-out;
    }
    @keyframes slideUp {
      from { opacity: 0; transform: translateY(24px) scale(0.98); }
      to { opacity: 1; transform: translateY(0); }
    }
    .aelixoria-chatbot-header {
      background:
        radial-gradient(circle at 80% -20%, rgba(200, 223, 255, 0.24), transparent 48%),
        linear-gradient(140deg, ${CONFIG.headerColor} 0%, #0f1b4a 100%);
      color: white;
      padding: 16px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    }
    .aelixoria-chatbot-header-info {
      display: flex;
      align-items: center;
      gap: 11px;
    }
    .aelixoria-chatbot-header-actions {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .aelixoria-chatbot-avatar {
      width: 48px;
      height: 48px;
      background: rgba(255, 255, 255, 0.12);
      border-radius: 15px;
      border: 1px solid rgba(255, 255, 255, 0.22);
      box-shadow: inset 0 1px 1px rgba(255, 255, 255, 0.25);
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .aelixoria-chatbot-avatar-image {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .aelixoria-chatbot-avatar-fallback {
      font-size: 24px;
    }
    .aelixoria-chatbot-agent-info h3 {
      margin: 0;
      font-size: 16px;
      line-height: 1.2;
      font-family: 'Space Grotesk', 'Manrope', sans-serif;
      font-weight: 700;
      letter-spacing: 0.01em;
    }
    .aelixoria-chatbot-agent-info p {
      margin: 0;
      font-size: 12px;
      opacity: 0.88;
    }
    .aelixoria-chatbot-header-btn {
      background: rgba(255, 255, 255, 0.12);
      border: none;
      color: white;
      cursor: pointer;
      padding: 6px;
      border-radius: 50%;
      transition: background 0.2s;
      display: flex;
      align-items: center;
      justify-content: center;
      width: 34px;
      height: 34px;
      border: 1px solid rgba(255, 255, 255, 0.15);
    }
    .aelixoria-chatbot-header-btn:hover {
      background: rgba(255, 255, 255, 0.24);
    }
    .aelixoria-chatbot-header-btn.call-active {
      background: #c73425;
      border-color: rgba(255, 255, 255, 0.25);
    }
    .aelixoria-chatbot-header-btn.call-active:hover {
      background: #b62a1c;
    }
    .aelixoria-chatbot-header-btn.call-ready {
      background: #0f8f4a;
      border-color: rgba(255, 255, 255, 0.25);
    }
    .aelixoria-chatbot-header-btn.call-ready:hover {
      background: #0c7a3f;
    }
    .aelixoria-chatbot-header-btn.call-connecting {
      background: #cf8f2f;
    }
    .aelixoria-chatbot-header-btn.muted {
      background: #b62a1c;
    }
    .aelixoria-chatbot-call-status {
      background: rgba(17, 138, 83, 0.13);
      border-bottom: 1px solid rgba(8, 10, 39, 0.12);
      padding: 12px 16px;
      display: none;
    }
    .aelixoria-chatbot-call-status.active {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .aelixoria-chatbot-call-status.connecting {
      background: rgba(207, 143, 47, 0.15);
      border-bottom-color: rgba(207, 143, 47, 0.38);
    }
    .aelixoria-chatbot-call-indicator {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      font-weight: 500;
      color: #166534;
    }
    .aelixoria-chatbot-call-status.connecting .aelixoria-chatbot-call-indicator {
      color: #854d0e;
    }
    .aelixoria-chatbot-call-dot {
      width: 8px;
      height: 8px;
      background: #0f8f4a;
      border-radius: 50%;
      animation: pulse-dot 2s infinite;
    }
    @keyframes pulse-dot {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.5; }
    }
    .aelixoria-chatbot-call-duration {
      font-size: 13px;
      font-weight: 600;
      font-family: 'Courier New', monospace;
      color: #166534;
    }
    .aelixoria-chatbot-call-status.connecting .aelixoria-chatbot-call-duration {
      color: #854d0e;
    }
    .aelixoria-chatbot-messages {
      flex: 1;
      overflow-y: auto;
      padding: 18px 16px;
      background:
        radial-gradient(circle at 100% 0%, rgba(8, 10, 39, 0.05), transparent 35%),
        linear-gradient(180deg, ${CONFIG.messagesBackground} 0%, #ece3d5 100%);
    }
    .aelixoria-chatbot-message {
      margin-bottom: 14px;
      display: flex;
      animation: fadeIn 0.3s ease-out;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(10px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .aelixoria-chatbot-message.user {
      justify-content: flex-end;
    }
    .aelixoria-chatbot-message-bubble {
      max-width: 80%;
      padding: 12px 15px;
      border-radius: 15px;
      word-wrap: break-word;
      line-height: 1.5;
      font-size: 14px;
      box-shadow: 0 8px 24px rgba(8, 10, 39, 0.1);
    }
    .aelixoria-chatbot-message.user .aelixoria-chatbot-message-bubble {
      background: linear-gradient(145deg, ${CONFIG.userBubbleColor} 0%, ${CONFIG.userBubbleColorAlt} 100%);
      color: white;
      border-bottom-right-radius: 6px;
      border: 1px solid rgba(255, 255, 255, 0.22);
    }
    .aelixoria-chatbot-message.assistant .aelixoria-chatbot-message-bubble {
      background-color: ${CONFIG.assistantBubbleColor};
      color: #1d293f;
      border: 1px solid rgba(8, 10, 39, 0.15);
      border-bottom-left-radius: 6px;
    }
    .aelixoria-chatbot-message-time {
      font-size: 11px;
      margin-top: 4px;
      opacity: 0.72;
    }
    .aelixoria-chatbot-typing {
      display: flex;
      gap: 4px;
      padding: 12px 16px;
    }
    .aelixoria-chatbot-typing-dot {
      width: 8px;
      height: 8px;
      background-color: #4f5a7d;
      border-radius: 50%;
      animation: typing 1.4s infinite;
    }
    .aelixoria-chatbot-typing-dot:nth-child(2) { animation-delay: 0.2s; }
    .aelixoria-chatbot-typing-dot:nth-child(3) { animation-delay: 0.4s; }
    @keyframes typing {
      0%, 60%, 100% { transform: translateY(0); }
      30% { transform: translateY(-10px); }
    }
    .aelixoria-chatbot-input-container {
      padding: 14px;
      background: rgba(251, 247, 239, 0.96);
      border-top: 1px solid rgba(8, 10, 39, 0.12);
      display: flex;
      gap: 8px;
    }
    .aelixoria-chatbot-input {
      flex: 1;
      padding: 12px 14px;
      border: 1px solid rgba(8, 10, 39, 0.2);
      border-radius: 24px;
      outline: none;
      font-size: 14px;
      font-family: 'Manrope', sans-serif;
      background: rgba(255, 255, 255, 0.8);
      color: #1b2442;
    }
    .aelixoria-chatbot-input:focus {
      border-color: ${CONFIG.primaryColor};
      box-shadow: 0 0 0 3px rgba(8, 10, 39, 0.14);
      background: #fff;
    }
    .aelixoria-chatbot-send {
      background:
        radial-gradient(circle at 30% 20%, rgba(255, 255, 255, 0.35), transparent 40%),
        linear-gradient(150deg, ${CONFIG.primaryColor} 0%, #0f1d4f 100%);
      color: white;
      border: none;
      border-radius: 50%;
      width: 44px;
      height: 44px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.2s;
      box-shadow: 0 10px 20px rgba(8, 10, 39, 0.25);
    }
    .aelixoria-chatbot-send:hover:not(:disabled) {
      transform: scale(1.06);
    }
    .aelixoria-chatbot-send:disabled {
      background-color: #d1d5db;
      cursor: not-allowed;
      box-shadow: none;
    }
    .aelixoria-chatbot-footer {
      padding: 8px 16px 10px;
      text-align: center;
      font-size: 11px;
      color: #4f5a7d;
      background: rgba(251, 247, 239, 0.96);
      border-top: 1px solid rgba(8, 10, 39, 0.1);
      letter-spacing: 0.03em;
    }
    @media (max-width: 640px) {
      .aelixoria-chatbot-window {
        width: 100%;
        height: 100%;
        ${posRight ? 'right: 0;' : 'left: 0;'}
        bottom: 0;
        border-radius: 0;
      }
      .aelixoria-chatbot-button {
        ${posRight ? 'right: 16px;' : 'left: 16px;'}
        bottom: 16px;
      }
    }
  `;

  // Injection des styles
  const styleSheet = document.createElement('style');
  styleSheet.textContent = styles;
  document.head.appendChild(styleSheet);

  // HTML du chatbot
  const chatbotHTML = `
    <button class="aelixoria-chatbot-button" id="aelixoria-chatbot-toggle">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
      </svg>
      ${CONFIG.enableNotificationBadge ? '<span class="aelixoria-chatbot-badge">1</span>' : ''}
    </button>
    <div class="aelixoria-chatbot-window" id="aelixoria-chatbot-window">
      <div class="aelixoria-chatbot-header">
        <div class="aelixoria-chatbot-header-info">
          <div class="aelixoria-chatbot-avatar">${getAgentAvatarMarkup()}</div>
          <div class="aelixoria-chatbot-agent-info">
            <h3>${escapeHtml(CONFIG.agentName)}</h3>
            <p id="aelixoria-agent-status">${escapeHtml(CONFIG.agentTitle)}</p>
          </div>
        </div>
        <div class="aelixoria-chatbot-header-actions">
          ${CONFIG.enableVoiceCall ? `
            <button class="aelixoria-chatbot-header-btn call-ready" id="aelixoria-call-btn" title="Appel vocal">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path>
              </svg>
            </button>
            <button class="aelixoria-chatbot-header-btn" id="aelixoria-mute-btn" title="Micro" style="display: none;">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
                <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                <line x1="12" y1="19" x2="12" y2="23"></line>
                <line x1="8" y1="23" x2="16" y2="23"></line>
              </svg>
            </button>
          ` : ''}
          <button class="aelixoria-chatbot-header-btn" id="aelixoria-chatbot-close">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
      </div>
      <div class="aelixoria-chatbot-call-status" id="aelixoria-call-status">
        <div class="aelixoria-chatbot-call-indicator">
          <div class="aelixoria-chatbot-call-dot"></div>
          <span id="aelixoria-call-status-text">Appel en cours</span>
        </div>
        <div class="aelixoria-chatbot-call-duration" id="aelixoria-call-duration">00:00</div>
      </div>
      <div class="aelixoria-chatbot-messages" id="aelixoria-chatbot-messages"></div>
      <div class="aelixoria-chatbot-input-container">
        <input type="text" class="aelixoria-chatbot-input" id="aelixoria-chatbot-input" placeholder="${escapeAttr(CONFIG.inputPlaceholder)}" autocomplete="off" />
        <button class="aelixoria-chatbot-send" id="aelixoria-chatbot-send">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="22" y1="2" x2="11" y2="13"></line>
            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
          </svg>
        </button>
      </div>
      <div class="aelixoria-chatbot-footer">${escapeHtml(CONFIG.poweredByText)}</div>
    </div>
  `;

  const container = document.createElement('div');
  container.innerHTML = chatbotHTML;
  document.body.appendChild(container);

  // État du chatbot
  let messages = [];
  let sessionId = null;
  let isTyping = false;

  // État de l'appel vocal
  let twilioDevice = null;
  let currentCall = null;
  let isCallActive = false;
  let isMuted = false;
  let callDuration = 0;
  let callDurationInterval = null;

  // ========================================
  // SESSION TOKEN MANAGEMENT (secure embed_key flow)
  // ========================================
  let _sessionToken = null;
  let _sessionExpiresAt = 0;
  let _sessionInitPromise = null;

  async function _ensureSession() {
    if (!CONFIG.embedKey) return; // Not using embed_key flow
    const now = Date.now() / 1000;
    if (_sessionToken && _sessionExpiresAt > now + 60) return; // Still valid (with 60s buffer)
    if (_sessionInitPromise) return _sessionInitPromise; // Already initializing

    _sessionInitPromise = (async () => {
      try {
        const endpoint = _sessionToken
          ? `${CONFIG.apiUrl}/widget/refresh`
          : `${CONFIG.apiUrl}/widget/init`;
        const body = _sessionToken
          ? { session_token: _sessionToken }
          : { embed_key: CONFIG.embedKey };
        const resp = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!resp.ok) throw new Error(`Widget auth failed: ${resp.status}`);
        const data = await resp.json();
        _sessionToken = data.session_token;
        _sessionExpiresAt = (Date.now() / 1000) + (data.expires_in || 1800);
      } catch (err) {
        console.error('Widget session init failed:', err);
        _sessionToken = null;
      } finally {
        _sessionInitPromise = null;
      }
    })();
    return _sessionInitPromise;
  }

  function _getAuthHeaders() {
    const headers = {};
    if (_sessionToken) {
      headers['X-Widget-Session'] = _sessionToken;
    } else if (CONFIG.widgetToken) {
      headers['X-Widget-Token'] = CONFIG.widgetToken;
    }
    return headers;
  }

  function withTenantAuth(url) {
    // If using embed_key flow, no query params needed (session token goes in header)
    if (CONFIG.embedKey && _sessionToken) return url;
    // Legacy flow: provider_key + tenant_token in query params
    if (!CONFIG.providerKey || !CONFIG.tenantToken) return url;
    try {
      const finalUrl = new URL(url, window.location.origin);
      finalUrl.searchParams.set('provider_key', CONFIG.providerKey);
      finalUrl.searchParams.set('tenant_token', CONFIG.tenantToken);
      return finalUrl.toString();
    } catch (_err) {
      const sep = url.includes('?') ? '&' : '?';
      return `${url}${sep}provider_key=${encodeURIComponent(CONFIG.providerKey)}&tenant_token=${encodeURIComponent(CONFIG.tenantToken)}`;
    }
  }

  // Éléments DOM
  const toggleButton = document.getElementById('aelixoria-chatbot-toggle');
  const chatWindow = document.getElementById('aelixoria-chatbot-window');
  const closeButton = document.getElementById('aelixoria-chatbot-close');
  const messagesContainer = document.getElementById('aelixoria-chatbot-messages');
  const input = document.getElementById('aelixoria-chatbot-input');
  const sendButton = document.getElementById('aelixoria-chatbot-send');
  const callButton = document.getElementById('aelixoria-call-btn');
  const muteButton = document.getElementById('aelixoria-mute-btn');
  const callStatus = document.getElementById('aelixoria-call-status');
  const callStatusText = document.getElementById('aelixoria-call-status-text');
  const callDurationEl = document.getElementById('aelixoria-call-duration');
  const agentStatus = document.getElementById('aelixoria-agent-status');

  // ========================================
  // FONCTIONS CHATBOT
  // ========================================
  function formatTime(date) {
    return date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  }

  function appendSafeRichText(target, rawText) {
    const text = rawText === null || rawText === undefined ? '' : String(rawText);
    const lines = text.split(/\r?\n/);
    const boldPattern = /\*\*(.+?)\*\*/g;

    lines.forEach((line, lineIndex) => {
      let cursor = 0;
      let match = boldPattern.exec(line);
      while (match) {
        if (match.index > cursor) {
          target.appendChild(document.createTextNode(line.slice(cursor, match.index)));
        }
        const strong = document.createElement('strong');
        strong.textContent = match[1];
        target.appendChild(strong);
        cursor = match.index + match[0].length;
        match = boldPattern.exec(line);
      }
      boldPattern.lastIndex = 0;
      if (cursor < line.length) {
        target.appendChild(document.createTextNode(line.slice(cursor)));
      }
      if (lineIndex < lines.length - 1) {
        target.appendChild(document.createElement('br'));
      }
    });
  }

  function showWelcomeMessage() {
    addMessage('assistant', CONFIG.welcomeMessage);
  }

  function addMessage(role, content) {
    const normalizedContent = content === null || content === undefined ? '' : String(content);
    messages.push({ role, content: normalizedContent, timestamp: new Date() });
    const messageDiv = document.createElement('div');
    messageDiv.className = `aelixoria-chatbot-message ${role}`;
    const bubbleDiv = document.createElement('div');
    bubbleDiv.className = 'aelixoria-chatbot-message-bubble';

    const contentDiv = document.createElement('div');
    appendSafeRichText(contentDiv, normalizedContent);

    const timeDiv = document.createElement('div');
    timeDiv.className = 'aelixoria-chatbot-message-time';
    timeDiv.textContent = formatTime(new Date());

    bubbleDiv.appendChild(contentDiv);
    bubbleDiv.appendChild(timeDiv);
    messageDiv.appendChild(bubbleDiv);
    messagesContainer.appendChild(messageDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }

  function showTyping() {
    if (isTyping) return;
    isTyping = true;
    const typingDiv = document.createElement('div');
    typingDiv.className = 'aelixoria-chatbot-message assistant';
    typingDiv.id = 'aelixoria-typing-indicator';
    typingDiv.innerHTML = `
      <div class="aelixoria-chatbot-message-bubble">
        <div class="aelixoria-chatbot-typing">
          <div class="aelixoria-chatbot-typing-dot"></div>
          <div class="aelixoria-chatbot-typing-dot"></div>
          <div class="aelixoria-chatbot-typing-dot"></div>
        </div>
      </div>
    `;
    messagesContainer.appendChild(typingDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }

  function hideTyping() {
    isTyping = false;
    const typingIndicator = document.getElementById('aelixoria-typing-indicator');
    if (typingIndicator) typingIndicator.remove();
  }

  async function sendMessage() {
    const message = input.value.trim();
    if (!message || isTyping) return;
    addMessage('user', message);
    input.value = '';
    sendButton.disabled = true;
    showTyping();
    try {
      await _ensureSession();
      const headers = { 'Content-Type': 'application/json', ..._getAuthHeaders() };
      const response = await fetch(withTenantAuth(`${CONFIG.apiUrl}/chat/chat`), {
        method: 'POST',
        headers,
        body: JSON.stringify({ message, session_id: sessionId })
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (data.session_id) sessionId = data.session_id;
      hideTyping();
      const reply = data.reply || data.message;
      addMessage('assistant', reply || 'Désolé, une erreur s\'est produite. Pouvez-vous reformuler ?');
    } catch (error) {
      console.error('Erreur chatbot:', error);
      hideTyping();
      addMessage('assistant', 'Désolé, une erreur s\'est produite. Pouvez-vous reformuler ?');
    } finally {
      sendButton.disabled = false;
      input.focus();
    }
  }

  // ========================================
  // FONCTIONS APPEL VOCAL
  // ========================================
  async function initializeTwilioDevice() {
    if (!CONFIG.enableVoiceCall) return;

    try {
      // Vérifier que Twilio.Device est chargé
      if (typeof Twilio === 'undefined' || !Twilio.Device) {
        console.error('Twilio SDK non chargé. Ajoutez: <script src="https://sdk.twilio.com/js/client/releases/1.14.0/twilio.min.js"></script>');
        callButton.disabled = true;
        callButton.title = 'Appel vocal indisponible (SDK Twilio manquant)';
        return;
      }

      // Récupérer le token depuis le backend
      await _ensureSession();
      const headers = { ..._getAuthHeaders() };
      const response = await fetch(withTenantAuth(`${CONFIG.apiUrl}/voice/token`), { headers });
      if (!response.ok) throw new Error('Impossible de récupérer le token Twilio');

      const { token } = await response.json();

      // Créer le Device Twilio
      twilioDevice = new Twilio.Device(token);

      // Événements du Device
      twilioDevice.on('ready', () => {
        console.log('✅ Twilio Device prêt');
        callButton.disabled = false;
      });

      twilioDevice.on('error', (error) => {
        console.error('❌ Erreur Twilio Device:', error);
        addMessage('assistant', 'Désolé, le système d\'appel vocal n\'est pas disponible pour le moment.');
      });

      twilioDevice.on('connect', (conn) => {
        console.log('📞 Appel connecté');
        currentCall = conn;
        isCallActive = true;
        updateCallUI();
        startCallDuration();
      });

      twilioDevice.on('disconnect', () => {
        console.log('📞 Appel terminé');
        endCall();
      });

    } catch (error) {
      console.error('Erreur initialisation Twilio:', error);
      callButton.disabled = true;
      callButton.title = 'Appel vocal indisponible';
    }
  }

  function startCall() {
    if (!twilioDevice || isCallActive) return;

    try {
      callButton.classList.remove('call-ready');
      callButton.classList.add('call-connecting');
      callStatus.classList.add('active', 'connecting');
      callStatusText.textContent = 'Connexion en cours...';
      agentStatus.textContent = 'Connexion...';

      // Démarrer l'appel vers l'agent IA
      twilioDevice.connect({ To: 'agent-ia' });

    } catch (error) {
      console.error('Erreur démarrage appel:', error);
      addMessage('assistant', 'Impossible de démarrer l\'appel. Veuillez réessayer.');
      resetCallUI();
    }
  }

  function endCall() {
    if (currentCall) {
      currentCall.disconnect();
    }

    isCallActive = false;
    isMuted = false;
    stopCallDuration();
    resetCallUI();
  }

  function toggleMute() {
    if (!currentCall) return;

    isMuted = !isMuted;
    currentCall.mute(isMuted);

    if (isMuted) {
      muteButton.classList.add('muted');
      muteButton.innerHTML = `
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="1" y1="1" x2="23" y2="23"></line>
          <path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6"></path>
          <path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23"></path>
          <line x1="12" y1="19" x2="12" y2="23"></line>
          <line x1="8" y1="23" x2="16" y2="23"></line>
        </svg>
      `;
    } else {
      muteButton.classList.remove('muted');
      muteButton.innerHTML = `
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
          <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
          <line x1="12" y1="19" x2="12" y2="23"></line>
          <line x1="8" y1="23" x2="16" y2="23"></line>
        </svg>
      `;
    }
  }

  function updateCallUI() {
    callButton.classList.remove('call-ready', 'call-connecting');
    callButton.classList.add('call-active');
    callButton.innerHTML = `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path>
        <line x1="23" y1="1" x2="1" y2="23"></line>
      </svg>
    `;

    callStatus.classList.remove('connecting');
    callStatus.classList.add('active');
    callStatusText.textContent = 'Appel en cours';

    muteButton.style.display = 'flex';

    const duration = formatCallDuration(callDuration);
    agentStatus.textContent = `En appel - ${duration}`;
  }

  function resetCallUI() {
    callButton.classList.remove('call-active', 'call-connecting');
    callButton.classList.add('call-ready');
    callButton.innerHTML = `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path>
      </svg>
    `;

    callStatus.classList.remove('active', 'connecting');
    muteButton.style.display = 'none';
    muteButton.classList.remove('muted');
    agentStatus.textContent = CONFIG.agentTitle;
    callDuration = 0;
  }

  function startCallDuration() {
    callDuration = 0;
    callDurationInterval = setInterval(() => {
      callDuration++;
      const duration = formatCallDuration(callDuration);
      callDurationEl.textContent = duration;
      agentStatus.textContent = `En appel - ${duration}`;
    }, 1000);
  }

  function stopCallDuration() {
    if (callDurationInterval) {
      clearInterval(callDurationInterval);
      callDurationInterval = null;
    }
  }

  function formatCallDuration(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }

  // ========================================
  // EVENT LISTENERS
  // ========================================
  toggleButton.addEventListener('click', () => {
    chatWindow.classList.add('open');
    toggleButton.style.display = 'none';
    if (messages.length === 0) showWelcomeMessage();
    input.focus();
  });

  closeButton.addEventListener('click', () => {
    chatWindow.classList.remove('open');
    toggleButton.style.display = 'flex';
  });

  sendButton.addEventListener('click', sendMessage);
  input.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendMessage();
  });

  if (callButton) {
    callButton.addEventListener('click', () => {
      if (isCallActive) {
        endCall();
      } else {
        startCall();
      }
    });
  }

  if (muteButton) {
    muteButton.addEventListener('click', toggleMute);
  }

  // Auto-open si configuré
  if (CONFIG.autoOpenDelay > 0) {
    setTimeout(() => toggleButton.click(), CONFIG.autoOpenDelay);
  }

  // Initialiser Twilio Device au chargement
  if (CONFIG.enableVoiceCall) {
    initializeTwilioDevice();
  }

  console.log('AELIXORIATech Chatbot Widget avec appel vocal charge');
})();
