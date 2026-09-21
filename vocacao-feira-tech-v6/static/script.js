/**
 * script.js — Front-end da 1ª Feira Tech dos Jovens da Vocação
 *
 * Fluxo desta revisão (foco em agilidade):
 *   1. CREDENCIAMENTO ÚNICO — feito uma só vez por aparelho.
 *   2. QR CODE DO STAND → abre direto a avaliação, sem novo cadastro.
 *   3. AVALIAÇÃO EM UM TOQUE — tocar na estrela já envia a nota.
 *   4. PÓS-AVALIAÇÃO → compartilhar foto, escanear o próximo stand ou
 *      ver o passaporte, sem voltar ao início.
 *   5. LEITOR DE QR EMBUTIDO — o visitante não sai do aplicativo.
 *   6. ESTÚDIO SOCIAL — foto do visitante em card Feed (1:1) ou Stories
 *      (9:16), publicável no Instagram, TikTok, Facebook, X, WhatsApp,
 *      LinkedIn e Telegram.
 */

(() => {
  // ── Utilitários básicos ────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  // Escapa texto vindo do servidor antes de usar em innerHTML (evita XSS).
  const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ESC_MAP[ch]);

  const courseLabels = {
    webdesign:   '🎨 Web Design',
    programacao: '💻 Programação',
    audiovisual: '🎬 Audiovisual',
    ppt:         '📋 Prep. para o Trabalho',
    // Compatibilidade com valores legados do JSON
    python: '💻 Programação',
    web:    '🎨 Web Design',
    audio:  '🎬 Audiovisual',
    outro:  '📋 Outro',
  };

  const profileLabels = {
    aluno:             '🎓 Aluno Vocação',
    funcionario:       '🏫 Funcionário',
    visitante_externo: '🌐 Visitante Externo',
    empresa:           '💼 Empresa',
  };

  const profileBarClasses = {
    aluno:             'bar-aluno',
    funcionario:       'bar-funcionario',
    visitante_externo: 'bar-visitante_externo',
    empresa:           'bar-empresa',
    desconhecido:      'bar-desconhecido',
  };

  const networkLabels = {
    instagram:         '📸 Instagram',
    instagram_stories: '⚡ Instagram Stories',
    tiktok:            '🎵 TikTok',
    facebook:          '👍 Facebook',
    x:                 '✕ X (Twitter)',
    whatsapp:          '💬 WhatsApp',
    linkedin:          '💼 LinkedIn',
    telegram:          '✈ Telegram',
    download:          '⬇ Imagem salva',
    outro:             '🔗 Outros',
  };

  // Estado global da visita atual
  const state = {
    stars: 0,
    visitId: null,
    startedAt: Date.now(),
    timer: null,
    stand: null,
    photo: null,
    format: 'feed',
    sending: false,
  };

  const path = location.pathname;

  const hashtags =
    '#feiratechvocacao #cursosvocacao #transformandovidas #vocacao ' +
    '#jovensvocacao #primeirafeiratechvocacao #conclusaocurso #partiunovoprojeto';

  const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);

  // ── Identificação do visitante ─────────────────────────────────────────
  function randomId() {
    // crypto.randomUUID só existe em contexto seguro (https ou localhost).
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
      return window.crypto.randomUUID().replaceAll('-', '');
    }
    if (window.crypto && window.crypto.getRandomValues) {
      const bytes = new Uint8Array(16);
      window.crypto.getRandomValues(bytes);
      return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
    }
    return `${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`;
  }

  function visitorKey() {
    let key = null;
    try {
      key = localStorage.getItem('feira-tech-visitor');
    } catch (err) {
      key = null;
    }
    if (!key) {
      key = `${Date.now().toString(36)}_${randomId()}`;
      try {
        localStorage.setItem('feira-tech-visitor', key);
      } catch (err) {
        /* navegação privada: a chave vale só para esta sessão */
      }
    }
    return key;
  }

  // ── Perfil do visitante (cadastro único) ───────────────────────────────
  function getLocalProfile() {
    try {
      const raw = localStorage.getItem('feira-tech-profile');
      return raw ? JSON.parse(raw) : null;
    } catch (err) {
      return null;
    }
  }

  function setLocalProfile(profile) {
    try {
      localStorage.setItem('feira-tech-profile', JSON.stringify(profile));
    } catch (err) {
      /* navegação privada */
    }
  }

  function clearLocalProfile() {
    try {
      localStorage.removeItem('feira-tech-profile');
    } catch (err) {
      /* ignora */
    }
  }

  // ── Requisições HTTP ───────────────────────────────────────────────────
  async function apiFetch(url, options = {}) {
    let res;
    try {
      res = await fetch(url, options);
    } catch (err) {
      throw new Error('Sem conexão com o servidor. Verifique a rede e tente de novo.');
    }

    let data = {};
    const text = await res.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (err) {
        data = { error: `Resposta inesperada do servidor (HTTP ${res.status}).` };
      }
    }

    if (!res.ok) {
      throw Object.assign(
        new Error(data.error || 'Não foi possível concluir a requisição.'),
        { status: res.status, data }
      );
    }
    return data;
  }

  function post(url, body) {
    return apiFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  // ── Navegação entre telas (SPA) ────────────────────────────────────────
  function showOnly(id) {
    document.querySelectorAll('.view').forEach((view) => {
      view.hidden = view.id !== id;
    });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function toast(message, duration = 2800) {
    const el = $('toast');
    el.textContent = message;
    el.classList.add('show');
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove('show'), duration);
  }

  function formatDuration(seconds) {
    if (!seconds) return '—';
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return m ? `${m}min ${s}s` : `${s}s`;
  }

  // ── Dados: Stands ──────────────────────────────────────────────────────
  let standsCache = null;

  async function fetchStands() {
    if (!standsCache) standsCache = await apiFetch('/api/stands');
    return standsCache;
  }

  async function loadStands() {
    standsCache = null;
    const stands = await fetchStands();
    const list = $('standList');
    const select = $('standSelect');

    if (list) list.innerHTML = '';
    if (select) select.innerHTML = '<option value="">Selecione seu stand</option>';
    if ($('emptyStands')) $('emptyStands').hidden = stands.length > 0;

    stands.forEach((stand) => {
      if (list) {
        const link = document.createElement('a');
        link.href = `/visitar/${stand.id}`;
        link.className = 'stand-row';
        link.dataset.course = stand.course;
        link.innerHTML = `
          <span class="course-dot course-${stand.course}"></span>
          <span>
            <strong></strong>
            <small>${courseLabels[stand.course] || stand.course}</small>
          </span>
          <b>→</b>
        `;
        link.querySelector('strong').textContent = stand.name;
        list.appendChild(link);
      }

      if (select) {
        select.add(new Option(
          `${stand.name} · ${courseLabels[stand.course] || stand.course}`,
          stand.id
        ));
      }
    });

    return stands;
  }

  // ── Dados: Mural Público ───────────────────────────────────────────────
  async function loadWall() {
    const supports = await apiFetch('/api/mural');
    const grid = $('wallGrid');
    grid.replaceChildren();
    $('wallCount').textContent = supports.length ? `${supports.length} apoios` : '';
    $('emptyWall').hidden = supports.length > 0;

    supports.forEach((s) => {
      const art = document.createElement('article');
      const msg = document.createElement('p');
      const foot = document.createElement('div');
      const proj = document.createElement('strong');
      const stars = document.createElement('span');
      msg.textContent = `"${s.message}"`;
      proj.textContent = s.stand_name;
      stars.textContent = `${s.stars} ★`;
      foot.append(proj, stars);
      art.append(msg, foot);
      grid.appendChild(art);
    });
  }

  // ══════════════════════════════════════════════════════════════════════
  // TELA 0: CREDENCIAMENTO ÚNICO
  // ══════════════════════════════════════════════════════════════════════
  /**
   * Cadastro feito uma única vez. Quando o visitante chega por um QR Code,
   * mostramos o stand no topo para ele saber que está no caminho certo, e
   * ao confirmar o cadastro a avaliação começa na hora.
   *
   * @param {Function} onComplete  chamado com o perfil salvo
   * @param {Object}   context     { standId } quando veio de um QR Code
   */
  function initCheckin(onComplete, context = {}) {
    showOnly('profileView');

    let selectedType = null;
    const selectedCourses = new Set();
    const selectedStands = new Set();
    let bound = false;

    // Contexto do QR Code lido
    if (context.standId) {
      $('scanContext').hidden = false;
      $('profileIntro').textContent =
        'Falta só este passo. Ao confirmar, sua avaliação deste stand começa na hora.';
      fetchStands()
        .then((stands) => {
          const stand = stands.find((s) => s.id === context.standId);
          $('scanContextStand').textContent = stand ? stand.name : 'Stand da feira';
        })
        .catch(() => { $('scanContextStand').textContent = 'Stand da feira'; });
    }

    // Pré-carrega a lista de stands para o aluno marcar o(s) seu(s)
    function renderStandChecklist(stands) {
      const box = $('alunoStandChecklist');
      box.replaceChildren();

      if (!stands.length) {
        box.innerHTML = '<p class="empty-dark">Nenhum stand cadastrado ainda — use o campo abaixo.</p>';
        return;
      }

      stands.forEach((stand) => {
        const item = document.createElement('label');
        item.className = 'stand-check-item';

        const input = document.createElement('input');
        input.type = 'checkbox';
        input.value = stand.id;
        input.checked = selectedStands.has(stand.id);
        input.addEventListener('change', () => {
          if (input.checked) selectedStands.add(stand.id);
          else selectedStands.delete(stand.id);
        });

        const info = document.createElement('span');
        const strong = document.createElement('strong');
        strong.textContent = stand.name;
        const small = document.createElement('small');
        small.textContent = courseLabels[stand.course] || stand.course;
        info.append(strong, small);

        item.append(input, info);
        box.appendChild(item);
      });
    }

    fetchStands()
      .then(renderStandChecklist)
      .catch(() => {
        $('alunoStandChecklist').innerHTML =
          '<p class="empty-dark">Não foi possível carregar — use o campo abaixo.</p>';
      });

    function bindOnce() {
      if (bound) return;
      bound = true;

      // Cursos: seleção múltipla — cada pílula alterna independente das outras
      $('coursePills').addEventListener('click', (e) => {
        const pill = e.target.closest('.course-pill');
        if (!pill) return;
        const curso = pill.dataset.course;
        if (selectedCourses.has(curso)) {
          selectedCourses.delete(curso);
          pill.classList.remove('selected');
        } else {
          selectedCourses.add(curso);
          pill.classList.add('selected');
        }
      });

      $('cancelCheckin').addEventListener('click', () => {
        $('checkinForm').hidden = true;
        document.querySelectorAll('.profile-card').forEach((c) => c.classList.remove('selected'));
        selectedType = null;
      });

      $('confirmCheckin').addEventListener('click', submit);
    }

    function updateContactRequirement() {
      $('contactLabel').innerHTML = selectedType === 'aluno'
        ? 'Telefone ou e-mail <span class="req">*</span>'
        : 'Telefone ou e-mail <em>(opcional)</em>';
    }

    async function submit() {
      if (!selectedType) { toast('Escolha o seu perfil.'); return; }

      const name = $('visitorName').value.trim();
      if (name.length < 2) {
        toast('Informe seu nome.');
        $('visitorName').focus();
        return;
      }

      const contact = $('visitorContact').value.trim();
      if (selectedType === 'aluno' && !contact) {
        toast('Informe um telefone ou e-mail para contato.');
        $('visitorContact').focus();
        return;
      }

      const payload = {
        visitor_key: visitorKey(),
        profile_type: selectedType,
        name,
        contact: contact || null,
        cursos: [],
        own_stand_ids: [],
        group_name: null,
        department: null,
        company_name: null,
      };

      if (selectedType === 'aluno') {
        if (!selectedCourses.size) {
          toast('Selecione ao menos um curso.');
          return;
        }
        payload.cursos = [...selectedCourses];

        const manualGroup = $('alunoGrupoManual').value.trim();
        if (!selectedStands.size && manualGroup.length < 2) {
          toast('Marque o stand do seu grupo ou digite o nome de um grupo ainda não cadastrado.');
          $('alunoGrupoManual').focus();
          return;
        }
        payload.own_stand_ids = [...selectedStands];
        payload.group_name = manualGroup || null;
      } else if (selectedType === 'funcionario') {
        payload.department = $('funcionarioSetor').value.trim() || null;
      } else if (selectedType === 'empresa') {
        payload.company_name = $('empresaNome').value.trim() || null;
      }

      const button = $('confirmCheckin');
      button.disabled = true;
      button.textContent = 'Salvando...';
      try {
        await post('/api/visitors/profile', payload);
        const profile = {
          profile_type: selectedType,
          name,
          cursos: payload.cursos,
          own_stand_ids: payload.own_stand_ids,
          department: payload.department,
          company_name: payload.company_name,
        };
        setLocalProfile(profile);
        onComplete(profile);
      } catch (err) {
        toast(err.message);
        button.disabled = false;
        button.textContent = 'Confirmar e continuar →';
      }
    }

    $('profileGrid').onclick = (e) => {
      const card = e.target.closest('.profile-card');
      if (!card) return;

      document.querySelectorAll('.profile-card').forEach((c) => c.classList.remove('selected'));
      card.classList.add('selected');
      selectedType = card.dataset.type;

      bindOnce();
      $('checkinForm').hidden = false;
      $('alunoFields').hidden = selectedType !== 'aluno';
      $('funcionarioFields').hidden = selectedType !== 'funcionario';
      $('empresaFields').hidden = selectedType !== 'empresa';
      updateContactRequirement();
      $('checkinTitle').textContent =
        selectedType === 'aluno' ? 'Nos conte do seu curso e grupo' : 'Quase lá';
      $('checkinForm').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    };
  }

  // ══════════════════════════════════════════════════════════════════════
  // TELA 1: Home
  // ══════════════════════════════════════════════════════════════════════
  function initHome(profile) {
    showOnly('homeView');

    const badge = $('visitorBadge');
    const nome = profile.name ? `${profile.name} · ` : '';
    const extra = profile.profile_type === 'empresa' && profile.company_name
      ? ` (${profile.company_name})`
      : '';
    $('badgeProfileLabel').textContent =
      nome + (profileLabels[profile.profile_type] || profile.profile_type) + extra;
    badge.hidden = false;

    $('changeProfile').onclick = () => {
      clearLocalProfile();
      initCheckin(initHome);
    };

    loadWall().catch(() => { $('publicWall').hidden = true; });

    // Prévia do progresso no passaporte
    apiFetch(`/api/visitors/passport?key=${encodeURIComponent(visitorKey())}`)
      .then((p) => {
        $('passportTeaser').textContent =
          `${p.rated} de ${p.available} stands avaliados`;
      })
      .catch(() => { /* silencioso */ });

    $('scanEntry').onclick = () => openScanner();

    $('visitorEntry').onclick = async () => {
      $('standBrowser').hidden = false;
      await loadStands();
      $('standBrowser').scrollIntoView({ behavior: 'smooth' });
    };

    $('closeBrowser').onclick = () => { $('standBrowser').hidden = true; };

    $('courseFilter').onclick = (e) => {
      const pill = e.target.closest('.filter-pill');
      if (!pill) return;
      document.querySelectorAll('.filter-pill').forEach((p) => p.classList.remove('active'));
      pill.classList.add('active');
      const filter = pill.dataset.filter;
      document.querySelectorAll('.stand-row').forEach((row) => {
        row.hidden = filter ? row.dataset.course !== filter : false;
      });
    };
  }

  // ══════════════════════════════════════════════════════════════════════
  // LEITOR DE QR CODE EMBUTIDO
  // ══════════════════════════════════════════════════════════════════════
  const scanner = { stream: null, running: false, detector: null, raf: null };

  function setScannerStatus(text) {
    const el = $('scannerStatus');
    if (el) el.textContent = text;
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const tag = document.createElement('script');
      tag.src = src;
      tag.onload = resolve;
      tag.onerror = () => reject(new Error('Falha ao carregar o leitor.'));
      document.head.appendChild(tag);
    });
  }

  async function openScanner() {
    const overlay = $('scannerOverlay');
    overlay.hidden = false;
    $('scannerFallback').hidden = true;
    setScannerStatus('Iniciando a câmera...');

    $('scannerClose').onclick = closeScanner;

    if (!window.isSecureContext && !['localhost', '127.0.0.1'].includes(location.hostname)) {
      setScannerStatus('A câmera só funciona em endereço seguro (https). Use a câmera do celular para ler o QR Code.');
      $('scannerFallback').hidden = false;
      return;
    }

    try {
      scanner.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' } },
        audio: false,
      });
    } catch (err) {
      setScannerStatus('Não conseguimos acessar a câmera. Autorize o acesso ou use a câmera do celular.');
      $('scannerFallback').hidden = false;
      return;
    }

    const video = $('scannerVideo');
    video.srcObject = scanner.stream;
    await video.play().catch(() => {});

    // Detector nativo (Android/Chrome) → mais rápido e sem download.
    if ('BarcodeDetector' in window) {
      try {
        scanner.detector = new window.BarcodeDetector({ formats: ['qr_code'] });
      } catch (err) {
        scanner.detector = null;
      }
    }

    // Fallback (iOS/Safari): biblioteca jsQR carregada sob demanda.
    if (!scanner.detector && !window.jsQR) {
      try {
        await loadScript('https://cdnjs.cloudflare.com/ajax/libs/jsQR/1.4.0/jsQR.min.js');
      } catch (err) {
        setScannerStatus('Seu navegador não lê QR Code aqui dentro. Use a câmera do celular — funciona igual.');
        $('scannerFallback').hidden = false;
        return;
      }
    }

    setScannerStatus('Procurando o QR Code...');
    scanner.running = true;
    scanLoop();
  }

  async function scanLoop() {
    if (!scanner.running) return;
    const video = $('scannerVideo');

    if (video.readyState === video.HAVE_ENOUGH_DATA) {
      try {
        if (scanner.detector) {
          const codes = await scanner.detector.detect(video);
          if (codes.length) return handleScan(codes[0].rawValue);
        } else if (window.jsQR) {
          const canvas = document.createElement('canvas');
          canvas.width = video.videoWidth;
          canvas.height = video.videoHeight;
          const ctx = canvas.getContext('2d', { willReadFrequently: true });
          ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
          const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
          const code = window.jsQR(image.data, image.width, image.height);
          if (code && code.data) return handleScan(code.data);
        }
      } catch (err) {
        /* continua tentando no próximo quadro */
      }
    }

    scanner.raf = setTimeout(() => requestAnimationFrame(scanLoop), 180);
  }

  function handleScan(raw) {
    const text = String(raw || '').trim();
    const match = text.match(/\/visitar\/([A-Za-z0-9_-]{6,})/);
    const standId = match ? match[1] : (/^[A-Za-z0-9]{8,24}$/.test(text) ? text : null);

    if (!standId) {
      setScannerStatus('Este QR Code não é de um stand da feira. Tente outro.');
      scanner.raf = setTimeout(() => requestAnimationFrame(scanLoop), 900);
      return;
    }

    setScannerStatus('Stand encontrado! Abrindo...');
    if (navigator.vibrate) navigator.vibrate(60);
    closeScanner();
    location.href = `/visitar/${standId}`;
  }

  function closeScanner() {
    scanner.running = false;
    clearTimeout(scanner.raf);
    if (scanner.stream) {
      scanner.stream.getTracks().forEach((track) => track.stop());
      scanner.stream = null;
    }
    const video = $('scannerVideo');
    if (video) video.srcObject = null;
    $('scannerOverlay').hidden = true;
  }

  // ══════════════════════════════════════════════════════════════════════
  // TELA 2: Avaliação do Stand (chegada pelo QR Code)
  // ══════════════════════════════════════════════════════════════════════
  async function initVisit(standId) {
    showOnly('visitView');
    $('ratingArea').hidden = false;
    $('successState').hidden = true;
    $('visitError').hidden = true;

    try {
      const stand = await apiFetch(`/api/stands/${standId}`);
      state.stand = stand;
      $('visitStandName').textContent = stand.name;
      $('visitCourse').textContent = courseLabels[stand.course] || stand.course;
      state.startedAt = Date.now();

      const started = await post(`/api/stands/${standId}/visits/start`, {
        visitor_key: visitorKey(),
      });
      state.visitId = started.visit_id;

      clearInterval(state.timer);
      state.timer = setInterval(() => {
        const total = Math.floor((Date.now() - state.startedAt) / 1000);
        const mm = String(Math.floor(total / 60)).padStart(2, '0');
        const ss = String(total % 60).padStart(2, '0');
        $('visitTimer').textContent = `${mm}:${ss}`;
      }, 1000);

    } catch (err) {
      $('ratingArea').hidden = true;
      const errorDiv = $('visitError');
      errorDiv.hidden = false;

      // TRAVA DE OURO: aluno tentando avaliar o próprio stand
      if (err.data?.own_stand) {
        errorDiv.querySelector('h2').textContent = '🚫 Stand do seu grupo';
      } else if (err.data?.duplicate) {
        errorDiv.querySelector('h2').textContent = '✓ Você já avaliou este stand';
      }
      errorDiv.querySelector('p').textContent = err.message;
      return;
    }

    // AVALIAÇÃO EM UM TOQUE: tocar na estrela já envia a nota.
    $('starPicker').onclick = (e) => {
      const btn = e.target.closest('button');
      if (!btn || state.sending) return;
      state.stars = Number(btn.dataset.stars);
      [...$('starPicker').children].forEach((star) => {
        star.classList.toggle('selected', Number(star.dataset.stars) <= state.stars);
      });
      submitRating(standId);
    };

    $('submitRating').onclick = () => submitRating(standId);
    $('openSocialStudio').onclick = () => openSocialStudio(standId);
    $('scanNextStand').onclick = () => openScanner();
  }

  async function submitRating(standId) {
    if (!state.visitId || !state.stars || state.sending) return;
    state.sending = true;
    $('oneTapHint').textContent = 'Registrando sua avaliação...';

    try {
      await post(`/api/stands/${standId}/visits/${state.visitId}/finish`, {
        visitor_key: visitorKey(),
        stars: state.stars,
      });
      clearInterval(state.timer);
      try { localStorage.setItem(`feira-tech-rated-${standId}`, '1'); } catch (err) { /* ignora */ }
      if (navigator.vibrate) navigator.vibrate(40);

      setupQuickShare(standId);
      updateSuccessPassport();

      $('ratingArea').hidden = true;
      $('successState').hidden = false;
    } catch (err) {
      toast(err.message);
      $('oneTapHint').textContent = 'Toque na estrela: a avaliação é enviada na hora.';
    } finally {
      state.sending = false;
    }
  }

  function updateSuccessPassport() {
    apiFetch(`/api/visitors/passport?key=${encodeURIComponent(visitorKey())}`)
      .then((p) => {
        $('successPassport').textContent = `Meu passaporte (${p.rated}/${p.available})`;
      })
      .catch(() => { /* silencioso */ });
  }

  // ── Legenda e compartilhamento rápido ──────────────────────────────────
  function shareCaption() {
    return (
      `Acabei de conhecer o projeto "${state.stand?.name}" na 1ª Feira Tech ` +
      `dos Jovens da Vocação e dei ${state.stars} estrelas! ` +
      `Ideias que transformam vidas merecem ser compartilhadas.\n\n${hashtags}`
    );
  }

  function eventUrl() {
    return `${location.origin}/`;
  }

  function setupQuickShare(standId) {
    const pageUrl = encodeURIComponent(eventUrl());
    const text = encodeURIComponent(shareCaption());

    $('shareWhatsapp').href = `https://wa.me/?text=${text}%20${pageUrl}`;
    $('shareTwitter').href  = `https://twitter.com/intent/tweet?text=${text}&url=${pageUrl}`;
    $('shareFacebook').href = `https://www.facebook.com/sharer/sharer.php?u=${pageUrl}`;

    [['shareWhatsapp', 'whatsapp'], ['shareTwitter', 'x'], ['shareFacebook', 'facebook']]
      .forEach(([id, network]) => {
        $(id).addEventListener('click', () => recordShare(standId, network), { once: true });
      });
  }

  async function recordShare(standId, network = 'outro') {
    try {
      await post(`/api/stands/${standId}/engagement/share`, {
        visitor_key: visitorKey(),
        network,
      });
    } catch {
      // Silencioso — o compartilhamento externo não deve ser bloqueado
    }
  }

  // ══════════════════════════════════════════════════════════════════════
  // TELA 3: Estúdio Social (foto do visitante → redes sociais)
  // ══════════════════════════════════════════════════════════════════════
  function wrapText(ctx, text, x, y, maxW, lineH) {
    let line = '';
    for (const word of String(text).split(' ')) {
      const test = `${line}${word} `;
      if (ctx.measureText(test).width > maxW && line) {
        ctx.fillText(line.trim(), x, y);
        line = `${word} `;
        y += lineH;
      } else {
        line = test;
      }
    }
    ctx.fillText(line.trim(), x, y);
    return y;
  }

  function drawCover(ctx, img, x, y, w, h) {
    // Recorte equivalente a "object-fit: cover"
    const scale = Math.max(w / img.width, h / img.height);
    const sw = w / scale;
    const sh = h / scale;
    ctx.drawImage(
      img,
      (img.width - sw) / 2, (img.height - sh) / 2, sw, sh,
      x, y, w, h
    );
  }

  function drawSocialCard() {
    const canvas = $('socialCanvas');
    const story = state.format === 'story';
    const W = 1080;
    const H = story ? 1920 : 1080;
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext('2d');

    // Fundo
    ctx.fillStyle = '#0e7692';
    ctx.fillRect(0, 0, W, H);

    ctx.fillStyle = '#f5e024';
    ctx.beginPath(); ctx.arc(W - 100, 90, 170, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#c31f5e';
    ctx.beginPath(); ctx.arc(90, H - 110, 120, 0, Math.PI * 2); ctx.fill();

    // Área da foto
    const photoBox = story
      ? { x: 60, y: 300, w: 960, h: 1080 }
      : { x: 70, y: 185, w: 940, h: 555 };

    ctx.fillStyle = '#073f53';
    ctx.beginPath(); ctx.roundRect(photoBox.x, photoBox.y, photoBox.w, photoBox.h, 28); ctx.fill();

    if (state.photo) {
      ctx.save();
      ctx.beginPath();
      ctx.roundRect(photoBox.x, photoBox.y, photoBox.w, photoBox.h, 28);
      ctx.clip();
      drawCover(ctx, state.photo, photoBox.x, photoBox.y, photoBox.w, photoBox.h);

      // Selo com a nota sobre a foto
      ctx.fillStyle = 'rgba(7, 63, 83, .85)';
      ctx.beginPath();
      ctx.roundRect(photoBox.x + 28, photoBox.y + photoBox.h - 118, 250, 90, 20);
      ctx.fill();
      ctx.fillStyle = '#f5e024';
      ctx.font = '800 56px sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText(`${state.stars} ★`, photoBox.x + 58, photoBox.y + photoBox.h - 54);
      ctx.restore();
    } else {
      ctx.fillStyle = '#b4d33a';
      ctx.font = `800 ${story ? 260 : 180}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.fillText(`${state.stars}★`, W / 2, photoBox.y + photoBox.h / 2 + (story ? 90 : 60));
      ctx.fillStyle = '#d8f3f7';
      ctx.font = '600 32px sans-serif';
      ctx.fillText('adicione sua foto do stand', W / 2, photoBox.y + photoBox.h - 60);
    }

    // Cabeçalho
    ctx.textAlign = 'left';
    ctx.fillStyle = '#ffffff';
    ctx.font = `800 ${story ? 48 : 44}px sans-serif`;
    wrapText(ctx, '1ª FEIRA TECH — JOVENS DA VOCAÇÃO', 70, story ? 150 : 100, W - 220, 56);

    // Chamada + nome do projeto
    const baseY = story ? photoBox.y + photoBox.h + 110 : 810;
    ctx.fillStyle = '#f5e024';
    ctx.font = '800 42px sans-serif';
    ctx.fillText('EU VIVI ESSA IDEIA.', 70, baseY);

    ctx.fillStyle = '#ffffff';
    ctx.font = `800 ${story ? 62 : 56}px sans-serif`;
    wrapText(ctx, state.stand?.name || '', 70, baseY + 65, 940, story ? 70 : 64);

    ctx.font = '600 26px sans-serif';
    ctx.fillStyle = '#d8f3f7';
    ctx.fillText(
      `#feiratechvocacao  •  ${state.stars} estrela${state.stars !== 1 ? 's' : ''}`,
      70, H - 55
    );
  }

  function canvasBlob() {
    return new Promise((resolve) => $('socialCanvas').toBlob(resolve, 'image/png'));
  }

  function cardFileName() {
    return `feira-tech-${state.format}.png`;
  }

  function downloadCard() {
    const link = document.createElement('a');
    link.download = cardFileName();
    link.href = $('socialCanvas').toDataURL('image/png');
    link.click();
  }

  async function copyCaption() {
    try {
      await navigator.clipboard.writeText(`${shareCaption()}\n${eventUrl()}`);
      return true;
    } catch (err) {
      return false;
    }
  }

  function showPostSteps(network) {
    const app = network === 'tiktok' ? 'TikTok'
      : network === 'instagram_stories' ? 'Instagram Stories' : 'Instagram';
    const steps = [
      'A imagem foi baixada para o seu aparelho (verifique a galeria ou os downloads).',
      'A legenda com as hashtags já está copiada.',
      `Abra o ${app} e crie a publicação escolhendo essa imagem.`,
      'Cole a legenda e publique. Marque o perfil da Vocação para aparecermos juntos!',
    ];
    const list = $('postStepsList');
    list.replaceChildren();
    steps.forEach((text) => {
      const li = document.createElement('li');
      li.textContent = text;
      list.appendChild(li);
    });
    $('postSteps').hidden = false;
    $('postSteps').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  /**
   * Publica em uma rede específica.
   *
   * Instagram e TikTok não aceitam publicação por link da web: o caminho
   * que realmente funciona é a folha de compartilhamento do celular
   * (Web Share API, que lista os dois aplicativos) e, quando ela não
   * existe, baixar a imagem + copiar a legenda + abrir o aplicativo.
   */
  async function shareToNetwork(network, standId) {
    const pageUrl = encodeURIComponent(eventUrl());
    const caption = shareCaption();
    const text = encodeURIComponent(caption);

    const webIntents = {
      facebook: `https://www.facebook.com/sharer/sharer.php?u=${pageUrl}`,
      x:        `https://twitter.com/intent/tweet?text=${text}&url=${pageUrl}`,
      linkedin: `https://www.linkedin.com/sharing/share-offsite/?url=${pageUrl}`,
      telegram: `https://t.me/share/url?url=${pageUrl}&text=${text}`,
      whatsapp: `https://wa.me/?text=${text}%20${pageUrl}`,
    };

    const appLinks = {
      instagram:         'instagram://app',
      instagram_stories: 'instagram://story-camera',
      tiktok:            'snssdk1233://',
    };

    recordShare(standId, network);

    // Caminho 1: folha de compartilhamento com a imagem (melhor experiência)
    const blob = await canvasBlob();
    if (blob && navigator.canShare) {
      const file = new File([blob], cardFileName(), { type: 'image/png' });
      if (navigator.canShare({ files: [file] })) {
        try {
          await navigator.share({
            title: '1ª Feira Tech Vocação',
            text: `${caption}\n${eventUrl()}`,
            files: [file],
          });
          return;
        } catch (err) {
          if (err.name === 'AbortError') return;
        }
      }
    }

    // Caminho 2: Instagram / TikTok → baixar + copiar legenda + abrir o app
    if (appLinks[network]) {
      downloadCard();
      const copied = await copyCaption();
      showPostSteps(network);
      toast(copied ? 'Imagem baixada e legenda copiada!' : 'Imagem baixada! Copie a legenda abaixo.');
      if (isMobile) {
        setTimeout(() => { window.location.href = appLinks[network]; }, 1200);
      }
      return;
    }

    // Caminho 3: redes com compartilhamento por link
    if (webIntents[network]) {
      window.open(webIntents[network], '_blank', 'noopener,noreferrer');
      if (network !== 'whatsapp') {
        downloadCard();
        toast('Card baixado — anexe na publicação para ficar ainda melhor.');
      }
    }
  }

  function openSocialStudio(standId) {
    showOnly('socialView');
    state.format = 'feed';
    document.querySelectorAll('.format-tab').forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.format === 'feed');
    });
    drawSocialCard();

    $('backToSuccess').onclick = (e) => {
      e.preventDefault();
      showOnly('visitView');
    };

    $('formatTabs').onclick = (e) => {
      const tab = e.target.closest('.format-tab');
      if (!tab) return;
      document.querySelectorAll('.format-tab').forEach((t) => t.classList.remove('active'));
      tab.classList.add('active');
      state.format = tab.dataset.format;
      drawSocialCard();
    };

    $('socialPhoto').onchange = () => {
      const file = $('socialPhoto').files[0];
      if (!file || !file.type.startsWith('image/') || file.size > 12 * 1024 * 1024) {
        toast('Escolha uma imagem de até 12 MB.');
        return;
      }
      const img = new Image();
      img.onload = () => {
        state.photo = img;
        drawSocialCard();
        $('removePhoto').hidden = false;
      };
      img.src = URL.createObjectURL(file);
    };

    $('removePhoto').onclick = () => {
      state.photo = null;
      $('socialPhoto').value = '';
      $('removePhoto').hidden = true;
      drawSocialCard();
    };

    $('socialGrid').onclick = (e) => {
      const chip = e.target.closest('.social-chip');
      if (!chip) return;
      shareToNetwork(chip.dataset.network, standId);
    };

    $('closePostSteps').onclick = () => { $('postSteps').hidden = true; };

    $('downloadCard').onclick = async () => {
      downloadCard();
      toast('Imagem salva no seu aparelho.');
      await recordShare(standId, 'download');
    };

    $('shareCard').onclick = async () => {
      const blob = await canvasBlob();
      if (!blob) { toast('Não foi possível criar o card.'); return; }
      const file = new File([blob], cardFileName(), { type: 'image/png' });
      try {
        if (navigator.share && navigator.canShare?.({ files: [file] })) {
          await navigator.share({
            title: '1ª Feira Tech Vocação',
            text: `${shareCaption()}\n${eventUrl()}`,
            files: [file],
          });
        } else {
          downloadCard();
          const copied = await copyCaption();
          toast(copied
            ? 'Imagem baixada e legenda copiada. Agora é só publicar!'
            : 'Imagem baixada. Copie a legenda no botão abaixo.');
        }
        await recordShare(standId, 'outro');
      } catch (err) {
        if (err.name !== 'AbortError') toast('Não foi possível abrir o compartilhamento.');
      }
    };

    $('copyCaption').onclick = async () => {
      toast(await copyCaption() ? 'Texto e hashtags copiados!' : 'Não foi possível copiar.');
    };

    $('supportForm').onsubmit = async (e) => {
      e.preventDefault();
      try {
        await post(`/api/stands/${standId}/engagement/support`, {
          visitor_key: visitorKey(),
          message: $('supportMessage').value.trim(),
          consent: $('supportConsent').checked,
        });
        $('supportForm').replaceChildren(
          Object.assign(document.createElement('p'), {
            className: 'support-sent',
            textContent: 'Apoio enviado! O expositor fará a aprovação antes de aparecer no mural.',
          })
        );
      } catch (err) {
        toast(err.message);
      }
    };
  }

  // ══════════════════════════════════════════════════════════════════════
  // TELA 3B: Passaporte do Visitante
  // ══════════════════════════════════════════════════════════════════════
  async function initPassport(profile) {
    showOnly('passportView');

    $('passportGreeting').textContent = profile?.name
      ? `Vamos lá, ${profile.name}!`
      : 'Sua jornada na feira';

    $('passportScan').onclick = () => openScanner();
    $('passportRefresh').onclick = () => load();

    async function load() {
      try {
        const data = await apiFetch(
          `/api/visitors/passport?key=${encodeURIComponent(visitorKey())}`
        );
        const pct = data.available ? Math.round((data.rated / data.available) * 100) : 0;
        $('passportFill').style.width = `${pct}%`;
        $('passportCount').textContent = `${data.rated} / ${data.available}`;

        const grid = $('passportGrid');
        grid.replaceChildren();
        $('passportEmpty').hidden = data.stands.length > 0;

        data.stands.forEach((stand) => {
          const card = document.createElement(stand.rated || stand.own ? 'div' : 'a');
          card.className = `passport-card${stand.rated ? ' done' : ''}${stand.own ? ' own' : ''}`;
          if (!stand.rated && !stand.own) card.href = `/visitar/${stand.id}`;

          const mark = document.createElement('span');
          mark.className = 'passport-stamp';
          mark.textContent = stand.own ? '🏠' : stand.rated ? '✓' : '○';

          const body = document.createElement('span');
          const name = document.createElement('strong');
          name.textContent = stand.name;
          const meta = document.createElement('small');
          meta.textContent = stand.own
            ? 'Seu stand'
            : stand.rated
              ? `Avaliado · ${stand.stars} ★`
              : courseLabels[stand.course] || stand.course;
          body.append(name, meta);

          card.append(mark, body);
          grid.appendChild(card);
        });
      } catch (err) {
        toast(err.message);
      }
    }

    await load();
  }

  // ══════════════════════════════════════════════════════════════════════
  // TELA 4: Área do Expositor
  // ══════════════════════════════════════════════════════════════════════
  async function initExhibitor() {
    showOnly('exhibitorView');
    await loadStands();

    $('accessForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const standId = $('standSelect').value;
      const code = $('accessCode').value.trim();
      if (!standId) { showFormMsg('Selecione um stand.'); return; }
      try {
        await post(`/api/stands/${standId}/access`, { access_code: code });
        sessionStorage.setItem(`feira-tech-code-${standId}`, code);
        location.href = `/stand/${standId}`;
      } catch (err) {
        showFormMsg(err.message);
      }
    });

    // Recuperação do código de acesso
    $('forgotCodeLink').onclick = () => {
      $('authLayout').hidden = true;
      $('recoveryPanel').hidden = false;
      const origem = $('standSelect');
      const destino = $('recoveryStand');
      destino.innerHTML = '';
      [...origem.options].forEach((opt) => destino.add(new Option(opt.text, opt.value)));
      destino.value = origem.value;
      $('recoveryPanel').scrollIntoView({ behavior: 'smooth' });
    };

    $('cancelRecovery').onclick = () => {
      $('recoveryPanel').hidden = true;
      $('authLayout').hidden = false;
    };

    $('sendRecovery').onclick = async () => {
      const standId = $('recoveryStand').value;
      const nome = $('recoveryName').value.trim();
      if (!standId) { toast('Selecione o seu stand.'); return; }
      if (nome.length < 2) { toast('Informe seu nome.'); $('recoveryName').focus(); return; }

      const btn = $('sendRecovery');
      btn.disabled = true;
      try {
        const resposta = await post(`/api/stands/${standId}/recovery/request`, {
          requester: nome,
        });
        $('recoverySent').hidden = false;
        toast(resposta.duplicate
          ? 'Já existe um pedido aberto para este stand.'
          : 'Pedido enviado à organização!');
      } catch (err) {
        toast(err.message);
        btn.disabled = false;
      }
    };

    $('createForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        const result = await post('/api/stands', {
          name: $('standName').value.trim(),
          course: $('courseSelect').value,
        });
        $('authLayout').hidden = true;
        $('newAccessCode').textContent = result.access_code;
        $('codeReveal').hidden = false;
        sessionStorage.setItem(`feira-tech-code-${result.stand.id}`, result.access_code);
        $('openNewDashboard').href = `/stand/${result.stand.id}`;
      } catch (err) {
        showFormMsg(err.message);
      }
    });
  }

  function showFormMsg(msg) {
    const el = $('formMessage');
    el.textContent = msg;
    el.hidden = false;
    el.scrollIntoView({ behavior: 'smooth' });
  }

  // ══════════════════════════════════════════════════════════════════════
  // TELA 5: Dashboard do Expositor
  // ══════════════════════════════════════════════════════════════════════
  async function initDashboard(standId) {
    showOnly('dashboardView');
    const code = sessionStorage.getItem(`feira-tech-code-${standId}`);
    if (!code) { location.replace('/expositor'); return; }

    const load = async () => {
      try {
        const report = await apiFetch(
          `/api/stands/${standId}/dashboard?code=${encodeURIComponent(code)}`
        );

        $('dashboardName').textContent = report.stand.name;
        $('dashboardCourse').textContent = courseLabels[report.stand.course] || report.stand.course;
        $('metricVisitors').textContent = report.visitors;
        $('metricRating').textContent = report.visitors ? report.average_rating.toFixed(1) : '—';
        $('metricAverageTime').textContent = formatDuration(report.average_duration_seconds);
        $('metricTotalTime').textContent = formatDuration(report.total_duration_seconds);
        $('metricShares').textContent = report.shares;
        $('metricSupports').textContent = report.approved_supports;

        renderDistribution(report.distribution, report.visitors);
        renderProfileBreakdown(report.by_profile || {}, report.visitors, 'profileBreakdown');
        renderNetworkBreakdown(report.shares_by_network || {}, report.shares, 'networkBreakdown');
        renderRecent(report.recent_visits);
        renderPending(report.pending_supports, standId, code, load);

        const qrUrl = `/api/stands/${standId}/qr?code=${encodeURIComponent(code)}`;
        $('standQr').src = qrUrl;
        $('downloadQr').href = qrUrl;
        $('exportCsv').href = `/api/stands/${standId}/export?code=${encodeURIComponent(code)}`;
      } catch {
        sessionStorage.removeItem(`feira-tech-code-${standId}`);
        location.replace('/expositor');
      }
    };

    $('refreshDashboard').addEventListener('click', load);
    await load();
  }

  function renderDistribution(dist, total) {
    $('ratingDistribution').innerHTML = [5, 4, 3, 2, 1]
      .map((star) => {
        const count = dist[String(star)] || 0;
        const pct = total ? Math.round((count / total) * 100) : 0;
        return `
          <div class="rating-row">
            <span>${star} ★</span>
            <div><i style="width:${pct}%"></i></div>
            <b>${count}</b>
          </div>`;
      })
      .join('');
  }

  function renderProfileBreakdown(byProfile, total, containerId) {
    const container = $(containerId);
    if (!container) return;

    const profileNameMap = {
      aluno:             '🎓 Aluno Vocação',
      funcionario:       '🏫 Funcionário',
      visitante_externo: '🌐 Visitante Externo',
      empresa:           '💼 Empresa',
      desconhecido:      '❓ Sem perfil',
    };

    const entries = Object.entries(byProfile).sort((a, b) => b[1] - a[1]);
    if (!entries.length) {
      container.innerHTML = '<p class="empty-dark">Nenhum dado por perfil ainda.</p>';
      return;
    }

    container.innerHTML = entries
      .map(([type, count]) => {
        const pct = total ? Math.round((count / total) * 100) : 0;
        const barClass = profileBarClasses[type] || 'bar-desconhecido';
        return `
          <div class="profile-bar-row">
            <span>${profileNameMap[type] || type}</span>
            <div class="bar-track"><div class="bar-fill ${barClass}" style="width:${pct}%"></div></div>
            <b>${count}</b>
          </div>`;
      })
      .join('');
  }

  function renderNetworkBreakdown(byNetwork, total, containerId) {
    const container = $(containerId);
    if (!container) return;

    const entries = Object.entries(byNetwork).sort((a, b) => b[1] - a[1]);
    if (!entries.length) {
      container.innerHTML = '<p class="empty-dark">Nenhum compartilhamento registrado ainda.</p>';
      return;
    }

    container.innerHTML = entries
      .map(([network, count]) => {
        const pct = total ? Math.round((count / total) * 100) : 0;
        return `
          <div class="profile-bar-row">
            <span>${networkLabels[network] || network}</span>
            <div class="bar-track"><div class="bar-fill bar-network" style="width:${pct}%"></div></div>
            <b>${count}</b>
          </div>`;
      })
      .join('');
  }

  function renderRecent(visits) {
    $('recentVisits').innerHTML = visits.length
      ? visits
          .map((v) => {
            const time = new Date(v.finished_at).toLocaleTimeString('pt-BR', {
              hour: '2-digit',
              minute: '2-digit',
            });
            const profileTag = profileLabels[v.profile_type] || v.profile_type || '';
            return `
              <div class="recent-row">
                <span>
                  <strong>${v.stars} ★</strong>
                  <small class="profile-tag">${profileTag}</small>
                  <small>${time}</small>
                </span>
                <b>${formatDuration(v.duration_seconds)}</b>
              </div>`;
          })
          .join('')
      : '<p class="empty-dark">As avaliações aparecerão aqui.</p>';
  }

  function renderPending(supports, standId, code, reload) {
    $('pendingCount').textContent = supports.length;
    const list = $('pendingSupports');
    list.replaceChildren();

    if (!supports.length) {
      list.innerHTML = '<p class="empty-dark">Nenhum apoio aguardando aprovação.</p>';
      return;
    }

    supports.forEach((s) => {
      const row = document.createElement('div');
      row.className = 'pending-row';

      const copy = document.createElement('div');
      const msg = document.createElement('p');
      msg.textContent = s.message;
      const meta = document.createElement('small');
      meta.textContent = `${s.stars} ★ · ${new Date(s.created_at).toLocaleString('pt-BR')}`;
      copy.append(msg, meta);

      const actions = document.createElement('div');
      actions.className = 'moderation-actions';

      [['approve', 'Aprovar', 'btn-primary'], ['reject', 'Rejeitar', 'btn-secondary']].forEach(
        ([action, label, cls]) => {
          const btn = document.createElement('button');
          btn.className = `btn ${cls}`;
          btn.textContent = label;
          btn.onclick = async () => {
            btn.disabled = true;
            try {
              await post(
                `/api/stands/${standId}/engagement/${s.id}/moderate`,
                { access_code: code, action }
              );
              await reload();
            } catch (err) {
              toast(err.message);
              btn.disabled = false;
            }
          };
          actions.appendChild(btn);
        }
      );

      row.append(copy, actions);
      list.appendChild(row);
    });
  }

  // ══════════════════════════════════════════════════════════════════════
  // TELA 6: Admin Global
  // ══════════════════════════════════════════════════════════════════════
  function initAdmin() {
    showOnly('adminView');

    $('adminLoginForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const password = $('adminPassword').value;
      try {
        const report = await apiFetch(
          `/api/admin/dashboard?password=${encodeURIComponent(password)}`
        );
        sessionStorage.setItem('feira-tech-admin', password);
        $('adminLoginArea').hidden = true;
        $('adminDashboardArea').hidden = false;
        renderAdminDashboard(report, password);
      } catch (err) {
        const errEl = $('adminLoginError');
        errEl.textContent = err.message;
        errEl.hidden = false;
      }
    });

    const savedPw = sessionStorage.getItem('feira-tech-admin');
    if (savedPw) {
      apiFetch(`/api/admin/dashboard?password=${encodeURIComponent(savedPw)}`)
        .then((report) => {
          $('adminLoginArea').hidden = true;
          $('adminDashboardArea').hidden = false;
          renderAdminDashboard(report, savedPw);
        })
        .catch(() => sessionStorage.removeItem('feira-tech-admin'));
    }
  }

  function renderAdminDashboard(report, password) {
    $('adminTotalStands').textContent = report.total_stands;
    $('adminTotalVisitors').textContent = report.total_visitors;
    $('adminGlobalAvg').textContent = report.total_ratings ? report.global_average.toFixed(1) : '—';
    $('adminCheckedIn').textContent = report.checked_in ?? 0;
    $('adminTotalShares').textContent = report.total_shares;
    $('adminTotalSupports').textContent = report.total_approved_supports;

    renderProfileBreakdown(report.by_profile || {}, report.total_visitors, 'adminProfileBreakdown');
    renderNetworkBreakdown(
      report.shares_by_network || {}, report.total_shares, 'adminNetworkBreakdown'
    );
    renderLeaderboard(report.leaderboard || []);

    $('adminExportCsv').href = `/api/admin/export?password=${encodeURIComponent(password)}`;
    $('adminExportVisitors').href =
      `/api/admin/visitors/export?password=${encodeURIComponent(password)}`;

    $('adminExportSupports').href =
      `/api/admin/supports/export?password=${encodeURIComponent(password)}`;
    $('adminBackup').href = `/api/admin/backup?password=${encodeURIComponent(password)}`;
    $('adminPrintQr').onclick = () => printQrSheet(report.leaderboard || [], password);

    // Recuperação de código dos expositores
    loadRecoveryRequests(password);
    fillResetSelect(report.leaderboard || []);
    $('manualResetBtn').onclick = () => {
      const standId = $('resetStandSelect').value;
      if (!standId) { toast('Selecione o stand.'); return; }
      resetStandCode(standId, password);
    };

    $('adminRefresh').onclick = async () => {
      const fresh = await apiFetch(
        `/api/admin/dashboard?password=${encodeURIComponent(password)}`
      );
      renderAdminDashboard(fresh, password);
      toast('Dados atualizados!');
    };
  }

  function fillResetSelect(stands) {
    const select = $('resetStandSelect');
    select.innerHTML = '';
    select.add(new Option('Selecione o stand', ''));
    stands.forEach((stand) => {
      select.add(new Option(
        `${stand.name} · ${courseLabels[stand.course] || stand.course}`,
        stand.id
      ));
    });
  }

  async function loadRecoveryRequests(password) {
    const list = $('recoveryRequests');
    try {
      const pedidos = await apiFetch(
        `/api/admin/recovery/requests?password=${encodeURIComponent(password)}`
      );
      $('recoveryCount').textContent = pedidos.length;
      list.replaceChildren();

      if (!pedidos.length) {
        list.innerHTML = '<p class="empty-dark">Nenhum expositor pediu novo código.</p>';
        return;
      }

      pedidos.forEach((pedido) => {
        const row = document.createElement('div');
        row.className = 'pending-row';

        const info = document.createElement('div');
        const titulo = document.createElement('p');
        titulo.textContent = pedido.stand_name;
        const meta = document.createElement('small');
        meta.textContent =
          `Pedido por ${pedido.requester} · ${new Date(pedido.created_at).toLocaleString('pt-BR')}`;
        info.append(titulo, meta);

        const actions = document.createElement('div');
        actions.className = 'moderation-actions';
        const btn = document.createElement('button');
        btn.className = 'btn btn-primary';
        btn.textContent = 'Gerar novo código';
        btn.onclick = () => {
          btn.disabled = true;
          resetStandCode(pedido.stand_id, password, pedido.id);
        };
        actions.appendChild(btn);

        row.append(info, actions);
        list.appendChild(row);
      });
    } catch (err) {
      list.innerHTML = '<p class="empty-dark">Não foi possível carregar os pedidos.</p>';
    }
  }

  /** Gera um código novo e o exibe uma única vez para o organizador. */
  async function resetStandCode(standId, password, requestId = null) {
    try {
      const resultado = await post(`/api/admin/stands/${standId}/reset-code`, {
        password,
        request_id: requestId,
      });
      $('resetCode').textContent = resultado.access_code;
      $('resetStandName').textContent = `Stand: ${resultado.stand.name}`;
      $('resetReveal').hidden = false;
      $('resetReveal').scrollIntoView({ behavior: 'smooth', block: 'center' });
      toast('Código gerado! Entregue ao grupo — o antigo deixou de valer.');
      await loadRecoveryRequests(password);
    } catch (err) {
      toast(err.message);
    }
  }

  /** Folha com o QR Code de todos os stands, pronta para imprimir e colar. */
  function printQrSheet(stands, password) {
    if (!stands.length) { toast('Nenhum stand cadastrado ainda.'); return; }

    const win = window.open('', '_blank');
    if (!win) { toast('Libere as janelas pop-up para imprimir a folha.'); return; }

    const safe = (text) => String(text).replace(/[<>&]/g, '');
    const cards = stands.map((stand) => `
      <article>
        <h2>${safe(stand.name)}</h2>
        <p>${safe(courseLabels[stand.course] || stand.course)}</p>
        <img src="/api/admin/qr/${safe(stand.id)}?password=${encodeURIComponent(password)}" alt="QR Code" />
        <small>Aponte a câmera e avalie este projeto</small>
      </article>`).join('');

    win.document.write(`<!doctype html>
      <html lang="pt-BR"><head><meta charset="utf-8" />
      <title>QR Codes — 1ª Feira Tech</title>
      <style>
        body { font-family: system-ui, sans-serif; margin: 24px; color: #073f53; }
        h1 { font-size: 20px; margin-bottom: 18px; }
        .sheet { display: grid; grid-template-columns: repeat(2, 1fr); gap: 18px; }
        article { border: 2px dashed #0e7692; border-radius: 14px; padding: 16px; text-align: center; page-break-inside: avoid; }
        article h2 { font-size: 18px; margin: 0 0 4px; }
        article p { margin: 0 0 8px; font-size: 13px; color: #0e7692; }
        img { width: 210px; height: 210px; }
        small { display: block; margin-top: 6px; font-size: 11px; color: #555; }
        @media print { .no-print { display: none; } }
      </style></head>
      <body>
        <h1>1ª Feira Tech dos Jovens da Vocação — QR Codes dos stands</h1>
        <button class="no-print" onclick="window.print()">Imprimir</button>
        <div class="sheet">${cards}</div>
      </body></html>`);
    win.document.close();
  }

  function renderLeaderboard(leaderboard) {
    const medals = ['🥇', '🥈', '🥉'];
    $('adminLeaderboard').innerHTML = leaderboard.length
      ? leaderboard
          .map((item, i) => {
            const medal = medals[i] ?? `#${i + 1}`;
            return `
              <div class="leaderboard-row">
                <span class="rank-medal">${medal}</span>
                <div>
                  <div class="leaderboard-name">${esc(item.name)}</div>
                  <div class="leaderboard-course">${courseLabels[item.course] || item.course}</div>
                </div>
                <div class="leaderboard-stars">
                  ${item.average_rating ? item.average_rating.toFixed(1) + ' ★' : '—'}
                </div>
                <div class="leaderboard-visitors">${item.visitors} visit.</div>
              </div>`;
          })
          .join('')
      : '<p class="empty-dark">Nenhum dado disponível ainda.</p>';
  }

  // ══════════════════════════════════════════════════════════════════════
  // TELA 7: Relatório da Feira (para coordenação / histórico)
  // ══════════════════════════════════════════════════════════════════════
  function initReport() {
    showOnly('reportView');

    const abrir = async (password) => {
      const report = await apiFetch(
        `/api/admin/report?password=${encodeURIComponent(password)}`
      );
      sessionStorage.setItem('feira-tech-admin', password);
      $('reportLoginForm').hidden = true;
      $('reportBody').hidden = false;
      renderReport(report);
    };

    $('reportPrint').onclick = () => window.print();

    $('reportLoginForm').onsubmit = async (e) => {
      e.preventDefault();
      try {
        await abrir($('reportPassword').value);
      } catch (err) {
        const el = $('reportLoginError');
        el.textContent = err.message;
        el.hidden = false;
      }
    };

    const salva = sessionStorage.getItem('feira-tech-admin');
    if (salva) abrir(salva).catch(() => sessionStorage.removeItem('feira-tech-admin'));
  }

  function metricCard(label, value, hint) {
    return `
      <article>
        <small>${label}</small>
        <strong>${value}</strong>
        <span>${hint}</span>
      </article>`;
  }

  function renderReport(r) {
    const janela = r.primeira_avaliacao
      ? `${new Date(r.primeira_avaliacao).toLocaleString('pt-BR')} até ` +
        `${new Date(r.ultima_avaliacao).toLocaleString('pt-BR')}`
      : 'sem avaliações registradas';
    $('reportGeneratedAt').textContent =
      `Gerado em ${new Date(r.gerado_em).toLocaleString('pt-BR')} · Período: ${janela}`;

    $('reportMetrics').innerHTML = [
      metricCard('AVALIAÇÕES', r.total_visitas, 'registradas'),
      metricCard('VISITANTES ÚNICOS', r.visitantes_unicos, 'aparelhos'),
      metricCard('CREDENCIADOS', r.credenciados, 'cadastros'),
      metricCard('NOTA MÉDIA', r.nota_media ? r.nota_media.toFixed(1) : '—', 'de 5 estrelas'),
      metricCard('SATISFAÇÃO', `${r.satisfacao_pct}%`, 'notas 4 ou 5'),
      metricCard('STANDS', `${r.stands_avaliados}/${r.total_stands}`, 'avaliados'),
      metricCard('TEMPO MÉDIO', formatDuration(r.tempo_medio), 'por visita'),
      metricCard('TEMPO TOTAL', formatDuration(r.tempo_total), 'de interação'),
      metricCard('COMPARTILHAMENTOS', r.compartilhamentos, 'nas redes'),
      metricCard('APOIOS NO MURAL', r.apoios_aprovados, 'publicados'),
    ].join('');

    renderDistribution(r.distribuicao, r.total_visitas);
    renderProfileBreakdown(r.por_perfil || {}, r.total_visitas, 'reportByProfile');
    renderNetworkBreakdown(r.shares_by_network || {}, r.compartilhamentos, 'reportNetworks');

    // Desempenho por curso
    const cursos = Object.entries(r.por_curso || {})
      .sort((a, b) => b[1].visitas - a[1].visitas);
    $('reportByCourse').innerHTML = cursos.length
      ? `<table>
          <thead><tr><th>Curso</th><th>Stands</th><th>Avaliações</th><th>Nota</th></tr></thead>
          <tbody>${cursos.map(([, c]) => `
            <tr>
              <td>${c.label}</td>
              <td>${c.stands}</td>
              <td>${c.visitas}</td>
              <td>${c.nota_media ? c.nota_media.toFixed(1) + ' ★' : '—'}</td>
            </tr>`).join('')}
          </tbody></table>`
      : '<p class="empty-dark">Sem dados por curso.</p>';

    // Movimento por horário
    const horas = Object.entries(r.por_hora || {});
    const pico = horas.length ? Math.max(...horas.map(([, n]) => n)) : 0;
    $('reportByHour').innerHTML = horas.length
      ? horas.map(([hora, n]) => `
          <div class="hour-col">
            <div class="hour-bar" style="height:${pico ? Math.round((n / pico) * 100) : 0}%"></div>
            <b>${n}</b>
            <small>${hora}</small>
          </div>`).join('')
      : '<p class="empty-dark">Sem movimento registrado ainda.</p>';

    // Pontos de atenção
    const alertas = [];
    if (r.sem_avaliacao?.length) {
      alertas.push(
        `⚠️ ${r.sem_avaliacao.length} stand(s) ainda sem nenhuma avaliação: ` +
        r.sem_avaliacao.map((s) => esc(s.name)).join(', ') + '.'
      );
    }
    if (r.apoios_pendentes) {
      alertas.push(`📝 ${r.apoios_pendentes} mensagem(ns) do mural aguardando aprovação dos expositores.`);
    }
    if (r.total_visitas && r.compartilhamentos === 0) {
      alertas.push('📣 Nenhum compartilhamento nas redes ainda — vale incentivar o estúdio social.');
    }
    if (!alertas.length) alertas.push('✅ Nenhum ponto de atenção: todos os stands receberam avaliações.');
    $('reportAlerts').innerHTML = alertas.map((a) => `<p>${a}</p>`).join('');

    // Ranking completo
    $('reportRanking').innerHTML = r.ranking?.length
      ? `<table>
          <thead><tr>
            <th>#</th><th>Projeto</th><th>Curso</th><th>Avaliações</th>
            <th>Nota</th><th>Tempo médio</th><th>Redes</th><th>Apoios</th>
          </tr></thead>
          <tbody>${r.ranking.map((item, i) => `
            <tr>
              <td>${i + 1}</td>
              <td><strong>${esc(item.name)}</strong></td>
              <td>${item.course_label}</td>
              <td>${item.visitas}</td>
              <td>${item.nota_media ? item.nota_media.toFixed(1) + ' ★' : '—'}</td>
              <td>${formatDuration(item.tempo_medio)}</td>
              <td>${item.compartilhamentos}</td>
              <td>${item.apoios}</td>
            </tr>`).join('')}
          </tbody></table>`
      : '<p class="empty-dark">Nenhum projeto cadastrado.</p>';
  }

  // ══════════════════════════════════════════════════════════════════════
  // Roteamento SPA
  // ══════════════════════════════════════════════════════════════════════
  function route() {
    // Rotas administrativas não exigem credenciamento de visitante
    if (path === '/admin')     { initAdmin(); return; }
    if (path === '/relatorio') { initReport(); return; }
    if (path === '/expositor') { initExhibitor(); return; }
    if (path.startsWith('/stand/')) { initDashboard(path.split('/')[2]); return; }

    const profile = getLocalProfile();

    // Chegada pelo QR Code do stand
    if (path.startsWith('/visitar/')) {
      const standId = path.split('/')[2];
      if (!profile) {
        // Cadastro único e, logo em seguida, a avaliação deste stand
        initCheckin(() => initVisit(standId), { standId });
      } else {
        initVisit(standId);
      }
      return;
    }

    if (path === '/passaporte') {
      if (!profile) { initCheckin((p) => initPassport(p)); }
      else { initPassport(profile); }
      return;
    }

    if (path === '/escanear') {
      if (!profile) { initCheckin((p) => { initHome(p); openScanner(); }); }
      else { initHome(profile); openScanner(); }
      return;
    }

    if (!profile) { initCheckin(initHome); } else { initHome(profile); }
  }

  route();
})();
