    const selectedFiles = new Set();

    // Player state
    let playerTimer = null;
    let playerCurrentFile = null;
    let playerInfo = null;
    let playerFrameIndex = 0;
    let playerPlaying = false;
    let playerExternalFiles = [];

    // Playlist pagination state
    window.playlistLastFiles = window.playlistLastFiles || [];
    window.playlistPage = window.playlistPage || 1;
    window.playlistPageSize = window.playlistPageSize || 20;
    window.playlistOrder = window.playlistOrder || [];
    let playlistDragFile = null;

    function stopPlayer() {
        playerPlaying = false;
        const btn = document.getElementById('playerBtnPlay');
        if (btn) btn.textContent = 'Play';
        if (playerTimer) {
            clearInterval(playerTimer);
            playerTimer = null;
        }
        const a = document.getElementById('playerAudio');
        if (a) {
            try { a.pause(); } catch {}
        }
    }

    function showPlayerNotice(msg, isError = false) {
        const el = document.getElementById('playerNotice');
        if (!el) return;
        el.style.display = msg ? 'block' : 'none';
        el.textContent = msg || '';
        el.style.borderColor = isError ? 'rgba(239, 68, 68, 0.25)' : 'rgba(245, 158, 11, 0.25)';
        el.style.background = isError ? 'rgba(239, 68, 68, 0.08)' : 'rgba(245, 158, 11, 0.08)';
        el.style.color = isError ? '#fecaca' : '#fde68a';
    }

    function setPlayerPlaceholderVisible(visible) {
        const ph = document.getElementById('playerPlaceholder');
        if (ph) ph.style.display = visible ? 'flex' : 'none';
        const img = document.getElementById('playerFrame');
        // Avoid showing a broken-image glyph inside the viewport when no frame is loaded.
        if (img) img.style.display = visible ? 'none' : '';
    }

    async function loadPlayableFiles() {
        stopPlayer();
        showPlayerNotice('Loading FTLV files...');
        const sel = document.getElementById('playerFileSelect');
        if (!sel) return;
        sel.innerHTML = '';

        try {
            const res = await fetch('/api/files');
            const files = await res.json();
            const playable = (files || []).filter(f => !f.parseError && f.fileName && f.mdVersion);

            // Placeholder option so we don't auto-load the first file.
            const ph = document.createElement('option');
            ph.value = '';
            ph.textContent = 'Select an FTLV file...';
            sel.appendChild(ph);

            // External files first (if any)
            (playerExternalFiles || []).forEach(x => {
                const opt = document.createElement('option');
                opt.value = `token:${x.token}`;
                opt.textContent = `External: ${x.fileName}`;
                sel.appendChild(opt);
            });

            if (playable.length === 0) {
                if ((playerExternalFiles || []).length === 0) {
                    showPlayerNotice('No playable FTLV files found. Convert an MP4/Image first, then refresh.', true);
                    setPlayerPlaceholderVisible(true);
                } else {
                    showPlayerNotice('');
                }
                playerCurrentFile = null;
                playerInfo = null;
                const img = document.getElementById('playerFrame');
                if (img) img.removeAttribute('src');
                setPlayerPlaceholderVisible(true);
                document.getElementById('playerFrameLabel').textContent = 'Frame: - / -';
                document.getElementById('playerMetaLabel').textContent = 'FPS: -';
                const seek = document.getElementById('playerSeek');
                seek.max = '0';
                seek.value = '0';
                return;
            }

            playable.forEach(f => {
                const opt = document.createElement('option');
                opt.value = f.fileName;
                opt.textContent = f.fileName;
                sel.appendChild(opt);
            });

            sel.value = '';
            playerCurrentFile = null;
            playerInfo = null;
            showPlayerNotice('');
            setPlayerPlaceholderVisible(true);
        } catch (e) {
            showPlayerNotice('Failed to load files: ' + e, true);
            setPlayerPlaceholderVisible(true);
        }
    }

    async function browsePlayerFile() {
        stopPlayer();
        showPlayerNotice('Opening file browser...');
        try {
            const res = await fetch('/api/player_browse');
            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.error || 'Browse failed');
            }
            if (!data.token) {
                showPlayerNotice('');
                return;
            }

            playerExternalFiles = playerExternalFiles || [];
            // Prevent duplicates by token
            playerExternalFiles = playerExternalFiles.filter(x => x.token !== data.token);
            playerExternalFiles.unshift({ token: data.token, fileName: data.fileName || 'FTLV' });

            await loadPlayableFiles();

            const sel = document.getElementById('playerFileSelect');
            sel.value = `token:${data.token}`;
            onPlayerFileChange();
        } catch (e) {
            showPlayerNotice('Browse error: ' + e, true);
        }
    }

    async function loadPlayerInfo(fileName) {
        stopPlayer();
        playerInfo = null;
        playerFrameIndex = 0;

        try {
            const q = (fileName || '').startsWith('token:')
                ? ('token=' + encodeURIComponent((fileName || '').slice('token:'.length)))
                : ('name=' + encodeURIComponent(fileName));
            const res = await fetch('/api/ftlv_info?' + q);
            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.error || 'Failed to read FTLV info');
            }

            playerInfo = data;
            const frameCount = parseInt(data.frameCount || '0', 10);
            const fps = Number(data.fps || 0);

            // Load audio as WAV (browser-playable)
            const a = document.getElementById('playerAudio');
            if (a) {
                a.onerror = () => showPlayerNotice('Audio decode failed (file may be silent or audio chunk not detected)', true);
                a.onloadeddata = () => { if (!playerPlaying) showPlayerNotice(''); };
                a.src = '/api/ftlv_audio_wav?' + q;
                try { a.load(); } catch {}
            }

            const seek = document.getElementById('playerSeek');
            seek.max = Math.max(0, frameCount - 1).toString();
            seek.value = '0';

            document.getElementById('playerMetaLabel').textContent = `FPS: ${fps ? fps.toFixed(2) : '-'}`;
            updatePlayerFrameLabel();
            showPlayerNotice('');
            setPlayerPlaceholderVisible(false);

            await showPlayerFrame(0);
        } catch (e) {
            showPlayerNotice('Player error: ' + e, true);
            setPlayerPlaceholderVisible(true);
        }
    }

    function updatePlayerFrameLabel() {
        const fc = parseInt(playerInfo?.frameCount || '0', 10);
        document.getElementById('playerFrameLabel').textContent = fc > 0 ? `Frame: ${playerFrameIndex + 1} / ${fc}` : 'Frame: - / -';
    }

    async function showPlayerFrame(frameIndex) {
        if (!playerCurrentFile) return;
        if (!playerInfo) return;

        const fc = parseInt(playerInfo.frameCount || '0', 10);
        if (fc <= 0) return;

        playerFrameIndex = Math.max(0, Math.min(fc - 1, frameIndex));
        const seek = document.getElementById('playerSeek');
        seek.value = playerFrameIndex.toString();
        updatePlayerFrameLabel();

        const img = document.getElementById('playerFrame');
        setPlayerPlaceholderVisible(false);
        const q = (playerCurrentFile || '').startsWith('token:')
            ? (`token=${encodeURIComponent((playerCurrentFile || '').slice('token:'.length))}`)
            : (`name=${encodeURIComponent(playerCurrentFile)}`);
        img.src = `/api/ftlv_frame?${q}&frame=${playerFrameIndex}`;
    }

    function onPlayerFileChange() {
        const sel = document.getElementById('playerFileSelect');
        if (!sel) return;
        playerCurrentFile = sel.value || null;
        if (playerCurrentFile) {
            setPlayerPlaceholderVisible(false);
            loadPlayerInfo(playerCurrentFile);
        } else {
            stopPlayer();
            playerInfo = null;
            const img = document.getElementById('playerFrame');
            if (img) img.removeAttribute('src');
            document.getElementById('playerFrameLabel').textContent = 'Frame: - / -';
            document.getElementById('playerMetaLabel').textContent = 'FPS: -';
            const seek = document.getElementById('playerSeek');
            seek.max = '0';
            seek.value = '0';
            setPlayerPlaceholderVisible(true);
        }
    }

    function onPlayerSpeedChange() {
        const a = document.getElementById('playerAudio');
        if (a) {
            const speed = Number(document.getElementById('playerSpeed').value || 1) || 1;
            try { a.playbackRate = speed; } catch {}
        }
    }

    function onPlayerSeekInput() {
        const wasPlaying = playerPlaying;
        if (!wasPlaying) stopPlayer();
        const seek = document.getElementById('playerSeek');
        const idx = parseInt(seek.value || '0', 10);
        showPlayerFrame(idx);

        const a = document.getElementById('playerAudio');
        if (a && playerInfo) {
            const fps = Number(playerInfo.fps || 0) || 20;
            try { a.currentTime = idx / fps; } catch {}
            if (wasPlaying) {
                try { a.play(); } catch {}
            }
        }
    }

    function playerStep(delta) {
        const wasPlaying = playerPlaying;
        if (!wasPlaying) stopPlayer();
        const next = playerFrameIndex + delta;
        showPlayerFrame(next);
        const a = document.getElementById('playerAudio');
        if (a && playerInfo) {
            const fps = Number(playerInfo.fps || 0) || 20;
            const fc = parseInt(playerInfo.frameCount || '0', 10) || 1;
            const idx = Math.max(0, Math.min(fc - 1, next));
            try { a.currentTime = idx / fps; } catch {}
            if (wasPlaying) {
                try { a.play(); } catch {}
            }
        }
    }

    function togglePlayer() {
        if (!playerInfo || !playerCurrentFile) return;
        const fc = parseInt(playerInfo.frameCount || '0', 10);
        if (fc <= 0) return;

        playerPlaying = !playerPlaying;
        document.getElementById('playerBtnPlay').textContent = playerPlaying ? 'Pause' : 'Play';

        if (!playerPlaying) {
            stopPlayer();
            return;
        }

        const fps = Number(playerInfo.fps || 0) || 20;
        const speed = Number(document.getElementById('playerSpeed').value || 1) || 1;
        const intervalMs = 30;

        const a = document.getElementById('playerAudio');
        if (a) {
            try { a.playbackRate = speed; } catch {}
            // Ensure audio and frame start from the same position.
            try { a.currentTime = playerFrameIndex / fps; } catch {}
            try { a.play(); } catch {}
        }

        playerTimer = setInterval(() => {
            const a2 = document.getElementById('playerAudio');
            if (a2 && !isNaN(a2.currentTime) && a2.readyState >= 2) {
                const idx = Math.max(0, Math.min(fc - 1, Math.floor(a2.currentTime * fps)));
                if (idx !== playerFrameIndex) showPlayerFrame(idx);
                return;
            }
            const next = (playerFrameIndex + 1) % fc;
            showPlayerFrame(next);
        }, intervalMs);
    }

    async function loadGenerator() {
        const res = await fetch('/api/generator');
        const cfg = await res.json();

        document.getElementById('genMaxEntries').value = cfg.max_entries ?? 0;
        document.getElementById('genHeaderStyle').value = cfg.header_style ?? 'count_fc';
        document.getElementById('genHeaderUsedSlots').value = cfg.header_used_slots ?? 7;
        document.getElementById('genRecordCount').value = cfg.record_count ?? 100;
        const out = document.getElementById('playlistDefaultOutputDir');
        if (out) out.value = cfg.playlist_output_directory ?? (cfg.target_directory ?? '');
        window.playlistOrder = Array.isArray(cfg.playlist_order) ? cfg.playlist_order.slice() : [];
        const fg = document.getElementById('fileGenTargetDir');
        if (fg) fg.value = cfg.filegen_output_directory ?? (cfg.target_directory ?? '');
        const mp4ExtDir = document.getElementById('mp4FtlvTargetDir');
        if (mp4ExtDir) mp4ExtDir.value = cfg.mp4ftlv_output_directory ?? (cfg.filegen_output_directory ?? (cfg.target_directory ?? ''));


        const headerText = `${cfg.header_style}:${cfg.header_used_slots}`;
        const maxText = cfg.header_style === 'count_fc' ? 'auto (all enabled)' : (cfg.max_entries ? `${cfg.max_entries}` : 'all');
        const ffmpegStatus = cfg.ffmpegAvailable
            ? `MP4 conversion ready (${cfg.ffmpegSource || 'detected'} ffmpeg).`
            : `MP4 conversion unavailable. ${cfg.ffmpegMessage || 'ffmpeg is missing.'}`;
        document.getElementById('genNotice').textContent =
            `Generator: maxEntries=${maxText}, header=${headerText}, totalSlots=${cfg.record_count}. ` +
            `Recommended: header_style=count_fc for most fans.`;
        const runtimeNotice = document.getElementById('fileGenRuntimeNotice');
        if (runtimeNotice) {
            runtimeNotice.textContent = ffmpegStatus;
            runtimeNotice.style.borderColor = cfg.ffmpegAvailable ? 'rgba(16, 185, 129, 0.25)' : 'rgba(239, 68, 68, 0.3)';
            runtimeNotice.style.color = cfg.ffmpegAvailable ? 'var(--text)' : '#fecaca';
            runtimeNotice.style.background = cfg.ffmpegAvailable ? 'rgba(16, 185, 129, 0.08)' : 'rgba(239, 68, 68, 0.08)';
        }
        const mp4ExtNotice = document.getElementById('mp4FtlvRuntimeNotice');
        if (mp4ExtNotice) {
            mp4ExtNotice.textContent = ffmpegStatus;
            mp4ExtNotice.style.borderColor = cfg.ffmpegAvailable ? 'rgba(16, 185, 129, 0.25)' : 'rgba(239, 68, 68, 0.3)';
            mp4ExtNotice.style.color = cfg.ffmpegAvailable ? 'var(--text)' : '#fecaca';
            mp4ExtNotice.style.background = cfg.ffmpegAvailable ? 'rgba(16, 185, 129, 0.08)' : 'rgba(239, 68, 68, 0.08)';
        }

        const maxInput = document.getElementById('genMaxEntries');
        if (cfg.header_style === 'count_fc') {
            maxInput.disabled = true;
            maxInput.title = 'count_fc uses all enabled files automatically';
        } else {
            maxInput.disabled = false;
            maxInput.title = '';
        }
    }

    async function saveGenerator(opts = { quiet: false }) {

        const headerStyle = document.getElementById('genHeaderStyle').value;
        const payload = {
            max_entries: headerStyle === 'count_fc' ? 0 : parseInt(document.getElementById('genMaxEntries').value || '0', 10),
            header_style: headerStyle,
            header_used_slots: parseInt(document.getElementById('genHeaderUsedSlots').value || '7', 10),
            record_count: parseInt(document.getElementById('genRecordCount').value || '100', 10)
        };

        const res = await fetch('/api/generator', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        if (!res.ok) {
            alert('Failed to save generator settings');
            return;
        }
        await loadGenerator();
        if (!opts?.quiet) alert('Generator settings saved');
    }

    async function browseTargetDirForFileGen() {
        await browseFileGenTargetDir();
    }

    async function browsePlaylistDefaultOutputDir() {
        try {
            const res = await fetch('/api/browse?mode=default_output');
            const data = await res.json();
            if (data.path) {
                const out = document.getElementById('playlistDefaultOutputDir');
                if (out) out.value = data.path;
                await savePlaylistOutputSettings({ quiet: true });
            } else if (data.error) {
                alert('Browse failed: ' + data.error);
            }
        } catch (e) {
            alert('Browse failed: ' + e);
        }
    }

    async function savePlaylistOutputSettings(opts = { quiet: false }) {
        const out = document.getElementById('playlistDefaultOutputDir');
        const payload = {
            playlist_output_directory: (out?.value || '').trim()
        };
        const res = await fetch('/api/generator', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        if (!res.ok) {
            showToast('Failed to save output settings', true);
            return;
        }
        await loadGenerator();
        if (!opts?.quiet) showToast('Output settings saved');
    }

    async function browseFileGenTargetDir() {
        try {
            const res = await fetch('/api/browse?mode=default_output');
            const data = await res.json();
            if (data.path) {
                const fg = document.getElementById('fileGenTargetDir');
                if (fg) fg.value = data.path;
                await saveFileGenOutputDir({ quiet: true });
                showToast('Target folder updated');
            } else if (data.error) {
                alert('Browse failed: ' + data.error);
            }
        } catch (e) {
            alert('Browse failed: ' + e);
        }
    }

    async function saveTargetDirForFileGen() {
        await saveFileGenOutputDir({ quiet: true });
    }

    async function saveFileGenOutputDir(opts = { quiet: false }) {
        const fg = document.getElementById('fileGenTargetDir');
        if (!fg) return;
        const val = fg.value.trim();
        const res = await fetch('/api/generator', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ filegen_output_directory: val })
        });
        if (!res.ok) {
            showToast('Failed to save File Generator folder', true);
            return;
        }
        await loadGenerator();
        if (!opts?.quiet) showToast('File Generator folder saved');
    }

    // Playlist Manager (workspace-based, no "source folder" setting)
    let playlistUiEntries = [];
    let playlistUiOutputDir = '';
    let playlistUiAskEachTime = true;
    let playlistUiReferencePath = null;
    // `playlistDragFile` is declared in the shared global state near the top of the script.

    async function loadFiles() {
        // Back-compat alias: playlist tab uses this name in a few places.
        return await loadPlaylistWorkspace();
    }

    async function loadPlaylistWorkspace() {
        try {
            const res = await fetch('/api/playlist');
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                showToast(data.error || 'Failed to load playlist workspace', true);
                return;
            }
            applyPlaylistSnapshot(data);
        } catch (e) {
            showToast('Failed to load playlist workspace', true);
        }
    }

    function applyPlaylistSnapshot(data) {
        playlistUiEntries = Array.isArray(data.entries) ? data.entries : [];
        playlistUiOutputDir = String(data.outputDir || '');
        playlistUiAskEachTime = !!data.askOutputEachTime;
        playlistUiReferencePath = data.referencePath || null;
        renderPlaylistWorkspace();

        const status = document.getElementById('refLisStatus');
        if (status) {
            if (playlistUiReferencePath) {
                const parts = String(playlistUiReferencePath).split('/').filter(Boolean);
                const where = parts.length ? parts.slice(-2).join('/') : String(playlistUiReferencePath);
                status.textContent = `Loaded FTL.LIS: ${where} · Working entries: ${playlistUiEntries.length}`;
            } else {
                status.textContent = `No FTL.LIS loaded · Working entries: ${playlistUiEntries.length}`;
            }
        }
    }

    function renderPlaylistWorkspace() {
        const host = document.getElementById('playlistOrderList');
        if (!host) return;
        const list = Array.isArray(playlistUiEntries) ? playlistUiEntries : [];
        if (list.length === 0) {
            host.innerHTML = '<div class="notice" style="margin:0;">Use <strong>Open Files</strong> or <strong>Load FTL.LIS</strong> to start.</div>';
            return;
        }

        const header = `
            <div class="order-toolbar">
                <div style="color: var(--text-dim); font-size: 0.9rem;">
                    Showing <strong style="color: var(--text);">${list.length}</strong> entry(ies). Drag to reorder. Included entries will be written to <strong>FTL.LIS</strong>.
                </div>
            </div>
        `;

        host.innerHTML = header + list.map((e, i) => {
            const name = escHtml(e.fileName || '');
            const fileLit = JSON.stringify(String(e.fileName || ''));
            const included = !!e.willBeIncluded;
            const enabled = !!e.enabled;
            const badge = enabled ? (included ? '<span class="status-badge badge-included">INCLUDED</span>' : '<span class="status-badge badge-skipped">SKIPPED</span>') : '<span class="status-badge badge-skipped">DISABLED</span>';
            const src = escHtml(e.source || 'reference');
            return `
                <div
                    class="order-item"
                    draggable="true"
                    data-file-name="${escHtml(e.fileName)}"
                    ondragstart='onPlaylistDragStart(event, ${fileLit})'
                    ondragend="onPlaylistDragEnd(event)"
                    ondragover="onPlaylistDragOver(event)"
                    ondragleave="onPlaylistDragLeave(event)"
                    ondrop='onPlaylistDrop(event, ${fileLit})'
                >
                    <span class="order-index">${i + 1}</span>
                    <div style="min-width:0; flex:1;">
                        <div style="display:flex; gap:0.6rem; align-items:center; flex-wrap:wrap;">
                            <div style="font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 520px;">${name || '(unnamed)'}</div>
                            ${badge}
                        </div>
                        <div style="margin-top: 0.25rem; color: var(--text-dim); font-size: 0.82rem;">Source: ${src}${e.path ? (' · ' + escHtml(String(e.path).split('/').slice(-2).join('/'))) : ''}</div>
                    </div>
                    <div style="display:flex; gap:0.6rem; align-items:center; flex-wrap:wrap;">
                        <label style="display:flex; gap:0.5rem; align-items:center; color: var(--text-dim); font-size:0.9rem;">
                            <span>Enabled</span>
                            <input type="checkbox" ${enabled ? 'checked' : ''} onchange='playlistToggleEnabled(${fileLit}, this.checked)'>
                        </label>
                        <button class="btn-danger" onclick='playlistRemoveEntry(${fileLit})'>Remove</button>
                    </div>
                </div>
            `;
        }).join('');
    }

    function onPlaylistDragStart(event, fileName) {
        playlistDragFile = fileName;
        const item = event.currentTarget;
        if (item) item.classList.add('dragging');
        if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', fileName);
        }
    }

    function onPlaylistDragEnd(event) {
        playlistDragFile = null;
        document.querySelectorAll('.order-item').forEach(el => {
            el.classList.remove('dragging');
            el.classList.remove('drop-target');
        });
    }

    function onPlaylistDragOver(event) {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
        const item = event.currentTarget;
        if (item) item.classList.add('drop-target');
    }

    function onPlaylistDragLeave(event) {
        const item = event.currentTarget;
        if (item) item.classList.remove('drop-target');
    }

    async function onPlaylistDrop(event, targetFileName) {
        event.preventDefault();
        const item = event.currentTarget;
        if (item) item.classList.remove('drop-target');
        const dragged = playlistDragFile || (event.dataTransfer ? event.dataTransfer.getData('text/plain') : '');
        if (!dragged || !targetFileName || dragged === targetFileName) return;

        const names = (playlistUiEntries || []).map(e => e.fileName);
        const from = names.indexOf(dragged);
        const to = names.indexOf(targetFileName);
        if (from < 0 || to < 0) return;
        names.splice(to, 0, names.splice(from, 1)[0]);

        const res = await fetch('/api/playlist/reorder', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ order: names })
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(body.error || 'Failed to reorder', true);
            return;
        }
        applyPlaylistSnapshot(body);
    }

    async function playlistAddFilesUpload(e) {
        const files = Array.from(e?.target?.files || []);
        try {
            if (!files.length) {
                showToast('No files selected', true);
                return;
            }
            // Client-side cap: only upload files that look like FTLV by magic bytes.
            const supported = [];
            for (const f of files) {
                if (await _playlistIsFtlvFile(f)) supported.push(f);
            }
            const ignored = files.length - supported.length;
            if (!supported.length) {
                showToast('No supported FTLV files selected', true);
                return;
            }
            if (ignored) {
                showToast(`Ignoring ${ignored} unsupported file(s)`, true);
            }

            const form = new FormData();
            supported.forEach(f => form.append('file', f));
            const res = await fetch('/api/playlist/add_files_upload', { method: 'POST', body: form });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) {
                showToast(body.error || 'Failed to add files', true);
                return;
            }
            applyPlaylistSnapshot(body);
            const added = Number(body.added ?? 0) || 0;
            const ignoredServer = Array.isArray(body.errors) ? body.errors.length : 0;
            if (added) showToast(`Added ${added} file(s)`);
            else if (ignoredServer) showToast('No FTLV files added (non-FTLV selections were ignored)', true);
            else showToast('No files added', true);
        } finally {
            try { e.target.value = ''; } catch {}
        }
    }

    // Folder import intentionally removed for cross-platform reliability.

    async function playlistClear() {
        const res = await fetch('/api/playlist/clear', { method: 'POST' });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(body.error || 'Failed to clear', true);
            return;
        }
        applyPlaylistSnapshot(body);
        showToast('Playlist cleared');
    }

    async function playlistLoadReferenceUpload(e) {
        const file = e.target.files?.[0];
        if (!file) return;
        try {
            const form = new FormData();
            form.append('file', file);
            const res = await fetch('/api/playlist/load_reference', { method: 'POST', body: form });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) {
                alert(body.error || 'Failed to load FTL.LIS');
                return;
            }
            applyPlaylistSnapshot(body);
            showToast(body.message || 'FTL.LIS loaded');
        } finally {
            e.target.value = '';
        }
    }

    async function _playlistIsFtlvFile(file) {
        try {
            const head = await file.slice(0, 4).arrayBuffer();
            const b = new Uint8Array(head);
            return b.length === 4 && b[0] === 0x46 && b[1] === 0x54 && b[2] === 0x4C && b[3] === 0x56; // "FTLV"
        } catch {
            return false;
        }
    }

    async function playlistLoadReferenceBrowse() {
        const res = await fetch('/api/playlist/load_reference/browse');
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(body.error || 'Browse failed', true);
            return;
        }
        applyPlaylistSnapshot(body);
        if (body.path) showToast('FTL.LIS loaded');
    }

    async function playlistToggleEnabled(fileName, enabled) {
        const res = await fetch('/api/playlist/toggle', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ fileName, enabled })
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(body.error || 'Failed to update', true);
            return;
        }
        applyPlaylistSnapshot(body);
    }

    async function playlistRemoveEntry(fileName) {
        const res = await fetch('/api/playlist/remove', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ fileName })
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(body.error || 'Failed to remove', true);
            return;
        }
        applyPlaylistSnapshot(body);
    }

    async function resetPlaylistOrder() {
        const names = (playlistUiEntries || []).map(e => e.fileName).filter(Boolean).slice().sort((a, b) => String(a).localeCompare(String(b)));
        const res = await fetch('/api/playlist/reorder', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ order: names })
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(body.error || 'Failed to reset order', true);
            return;
        }
        applyPlaylistSnapshot(body);
        showToast('Order reset');
    }

    function openPlaylistGenerateModal(defaultDir) {
        const overlay = document.getElementById('playlistGenerateModal');
        const input = document.getElementById('playlistModalOutputDir');
        if (input) input.value = defaultDir || '';
        if (overlay) overlay.style.display = 'flex';
        setTimeout(() => { try { input?.focus(); } catch {} }, 50);
    }

    function closePlaylistGenerateModal() {
        const overlay = document.getElementById('playlistGenerateModal');
        if (overlay) overlay.style.display = 'none';
    }

    async function browsePlaylistModalOutputDir() {
        try {
            const res = await fetch('/api/browse?mode=default_output');
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                showToast(data.error || 'Browse failed', true);
                return;
            }
            if (!data.path) return;
            const input = document.getElementById('playlistModalOutputDir');
            if (input) {
                input.value = data.path;
                try { input.focus(); } catch {}
            }
        } catch (e) {
            showToast('Browse failed', true);
        }
    }

    // Shared output-directory modal (used by all generators/converters)
    let outputDirModalSubmit = null; // (dir: string) => void
    let outputDirModalCancel = null; // () => void

    function openOutputDirModal(opts = {}) {
        const overlay = document.getElementById('outputDirModal');
        const input = document.getElementById('outputDirModalInput');
        const title = document.getElementById('outputDirModalTitle');
        const subtitle = document.getElementById('outputDirModalSubtitle');
        if (title) title.textContent = opts.title || 'Choose Output Folder';
        if (subtitle) subtitle.textContent = opts.subtitle || 'Select where output files should be saved.';
        if (input) input.value = (opts.defaultDir || '').trim();
        outputDirModalSubmit = typeof opts.onSubmit === 'function' ? opts.onSubmit : null;
        outputDirModalCancel = typeof opts.onCancel === 'function' ? opts.onCancel : null;
        if (overlay) overlay.style.display = 'flex';
        setTimeout(() => { try { input?.focus(); } catch {} }, 50);
    }

    function closeOutputDirModal() {
        const overlay = document.getElementById('outputDirModal');
        if (overlay) overlay.style.display = 'none';
        const cancel = outputDirModalCancel;
        outputDirModalSubmit = null;
        outputDirModalCancel = null;
        try { cancel && cancel(); } catch {}
    }

    async function browseOutputDirModal() {
        try {
            const res = await fetch('/api/browse?mode=default_output');
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                showToast(data.error || 'Browse failed', true);
                return;
            }
            if (!data.path) return;
            const input = document.getElementById('outputDirModalInput');
            if (input) {
                input.value = data.path;
                try { input.focus(); } catch {}
            }
        } catch (e) {
            showToast('Browse failed', true);
        }
    }

    function submitOutputDirModal() {
        const input = document.getElementById('outputDirModalInput');
        const dir = (input?.value || '').trim();
        if (!dir) {
            showToast('Select an output folder', true);
            return;
        }
        const fn = outputDirModalSubmit;
        closeOutputDirModal();
        try {
            fn && fn(dir);
        } catch (e) {
            showToast('Could not start: ' + (e?.message || e), true);
        }
    }

    async function playlistSyncGenerate() {
        // Always re-fetch to ensure we have the latest default settings.
        const res = await fetch('/api/playlist');
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(body.error || 'Failed to prepare generation', true);
            return;
        }
        applyPlaylistSnapshot(body);
        if (!playlistUiEntries.length) {
            showToast('No entries to generate', true);
            return;
        }
        const dir = String(body.outputDir || '');
        // Always ask for output folder when generating.
        openPlaylistGenerateModal(dir || '');
    }

    async function playlistSubmitGenerate() {
        const input = document.getElementById('playlistModalOutputDir');
        const dir = (input?.value || '').trim();
        if (!dir) {
            showToast('Select an output folder', true);
            return;
        }
        closePlaylistGenerateModal();
        await playlistGenerate(dir);
    }

    async function playlistGenerate(outputDir) {
        const res = await fetch('/api/playlist/generate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ outputDir, remember: false, dontAskAgain: false })
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(body.error || 'Generation failed', true);
            return;
        }
        applyPlaylistSnapshot(body);
        await loadGenerator();
        showToast(body.message || 'FTL.LIS generated');
    }

    function getActiveTabName() {
        if (document.getElementById('tabPlaylist')?.classList.contains('active')) return 'playlist';
        if (document.getElementById('tabFileGen')?.classList.contains('active')) return 'filegen';
        if (document.getElementById('tabMp4ToFtlv')?.classList.contains('active')) return 'mp4ftlv';
        if (document.getElementById('tabPlayer')?.classList.contains('active')) return 'player';
        if (document.getElementById('tabConfig')?.classList.contains('active')) return 'config';
        return 'playlist';
    }

    function refreshCurrentView() {
        const tab = getActiveTabName();
        if (tab === 'playlist') {
            loadPlaylistWorkspace();
            loadGenerator();
            return;
        }
        if (tab === 'filegen') {
            loadTasks();
            return;
        }
        if (tab === 'mp4ftlv') {
            loadMp4FtlvTasks();
            return;
        }
        if (tab === 'player') {
            loadPlayableFiles();
            return;
        }
        if (tab === 'config') {
            loadConfig();
        }
    }

    function getTheme() {
        const stored = localStorage.getItem('theme');
        if (stored === 'light' || stored === 'dark') return stored;
        return 'dark';
    }

    function applyTheme(theme) {
        const t = theme === 'light' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', t);
        localStorage.setItem('theme', t);
        const text = document.getElementById('themeToggleText');
        const icon = document.getElementById('themeToggleIcon');
        if (text) text.textContent = t === 'light' ? 'Light' : 'Dark';
        if (icon) icon.src = t === 'light' ? '/static/icons/sun.svg' : '/static/icons/moon.svg';
    }

    function toggleTheme() {
        applyTheme(getTheme() === 'light' ? 'dark' : 'light');
    }

    function getViewportMode() {
        const stored = localStorage.getItem('viewportMode');
        if (stored === 'circle' || stored === 'square') return stored;
        return 'square';
    }

    function applyViewportMode(mode) {
        const m = mode === 'circle' ? 'circle' : 'square';
        document.documentElement.setAttribute('data-viewport', m);
        localStorage.setItem('viewportMode', m);
        document.querySelectorAll('.viewport-toggle-label').forEach((el) => {
            el.textContent = m === 'circle' ? 'Circle' : 'Square';
        });
    }

    function toggleViewportMode() {
        applyViewportMode(getViewportMode() === 'circle' ? 'square' : 'circle');
    }

    function updateTopbarUploadButton() {
        const btn = document.getElementById('topbarUploadBtn');
        const folderBtn = document.getElementById('topbarUploadFolderBtn');
        if (!btn || !folderBtn) return;
        const tab = getActiveTabName();
        const showForFileGen = tab === 'filegen' && pendingMediaFiles.length > 0 && !isFileGenHistoryMode();
        const showForMp4 = tab === 'mp4ftlv' && mp4FtlvPendingFiles.length > 0;
        const shouldShow = showForFileGen || showForMp4;
        btn.style.display = shouldShow ? 'inline-flex' : 'none';
        folderBtn.style.display = shouldShow ? 'inline-flex' : 'none';
        btn.textContent = 'Upload Files';
        folderBtn.textContent = 'Upload Folder';
    }

    function triggerTopbarUpload() {
        const tab = getActiveTabName();
        if (tab === 'filegen') {
            document.getElementById('mediaInput')?.click();
            return;
        }
        if (tab === 'mp4ftlv') {
            document.getElementById('mp4FtlvInput')?.click();
        }
    }

    function triggerTopbarUploadFolder() {
        const tab = getActiveTabName();
        if (tab === 'filegen') {
            document.getElementById('mediaFolderInput')?.click();
            return;
        }
        if (tab === 'mp4ftlv') {
            document.getElementById('mp4FtlvFolderInput')?.click();
        }
    }

    async function clearServerScreenState(scope) {
        const res = await fetch('/api/clear_screen_state', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scope })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || 'Failed to clear screen state');
        }
    }

    async function clearPlaylistScreen() {
        try { selectedFiles.clear(); } catch {}
        try { window.playlistLastFiles = []; } catch {}
        try { window.playlistOrder = []; } catch {}

        const fi = document.getElementById('fileInput');
        if (fi) fi.value = '';

        const fileList = document.getElementById('fileList');
        if (fileList) fileList.innerHTML = '<p style="text-align:center; color: var(--text-dim);">Cleared. Click Refresh to load again.</p>';

        const playlistOrderList = document.getElementById('playlistOrderList');
        if (playlistOrderList) playlistOrderList.innerHTML = '<div class="notice" style="margin:0;">Cleared. Click Refresh to load again.</div>';

        const refLisStatus = document.getElementById('refLisStatus');
        if (refLisStatus) refLisStatus.textContent = 'Reference: (cleared on screen)';
    }

    async function clearFileGenScreen() {
        await clearServerScreenState('filegen');
        pendingMediaFiles = [];

        const mediaInput = document.getElementById('mediaInput');
        if (mediaInput) mediaInput.value = '';

        const taskList = document.getElementById('taskList');
        if (taskList) taskList.innerHTML = '<div class="notice" style="margin:0;">Cleared. Click Refresh to load again.</div>';

        const selectedFilesList = document.getElementById('selectedFilesList');
        if (selectedFilesList) selectedFilesList.innerHTML = '';

        const historyCard = document.getElementById('historyCard');
        if (historyCard) historyCard.style.display = 'none';

        const selectionArea = document.getElementById('selectionArea');
        if (selectionArea) selectionArea.style.display = 'none';
        const topbar = document.getElementById('fileGenLoadedBar');
        if (topbar) topbar.classList.remove('open');
        const dropZone = document.getElementById('genDropZone');
        if (dropZone) dropZone.style.display = 'block';

        const btnStartConversion = document.getElementById('btnStartConversion');
        if (btnStartConversion) btnStartConversion.textContent = 'Start Conversion (0 files)';
        const btnStartConversionTop = document.getElementById('btnStartConversionTop');
        if (btnStartConversionTop) btnStartConversionTop.textContent = 'Start Conversion';
        updateTopbarUploadButton();

        const saved = document.getElementById('fileGenSavedPath');
        if (saved) saved.textContent = '';
    }

    async function clearMp4FtlvScreen() {
        await clearServerScreenState('mp4ftlv');
        mp4FtlvPendingFiles = [];

        const mp4FtlvInput = document.getElementById('mp4FtlvInput');
        if (mp4FtlvInput) mp4FtlvInput.value = '';

        const mp4TaskList = document.getElementById('mp4FtlvTaskList');
        if (mp4TaskList) mp4TaskList.innerHTML = '<div class="notice" style="margin:0;">Cleared. Click Refresh to load again.</div>';

        const mp4SelectedFilesList = document.getElementById('mp4FtlvSelectedFilesList');
        if (mp4SelectedFilesList) mp4SelectedFilesList.innerHTML = '';

        const mp4HistoryCard = document.getElementById('mp4FtlvHistoryCard');
        if (mp4HistoryCard) mp4HistoryCard.style.display = 'none';

        const mp4SelectionArea = document.getElementById('mp4FtlvSelectionArea');
        if (mp4SelectionArea) mp4SelectionArea.style.display = 'none';
        const topbar = document.getElementById('mp4FtlvLoadedBar');
        if (topbar) topbar.classList.remove('open');
        const dropZone = document.getElementById('mp4FtlvDropZone');
        if (dropZone) dropZone.style.display = 'block';

        const btnStartMp4FtlvConversion = document.getElementById('btnStartMp4FtlvConversion');
        if (btnStartMp4FtlvConversion) btnStartMp4FtlvConversion.textContent = 'Generate .ftlv (0 files)';
        const btnStartMp4FtlvConversionTop = document.getElementById('btnStartMp4FtlvConversionTop');
        if (btnStartMp4FtlvConversionTop) btnStartMp4FtlvConversionTop.textContent = 'Generate .ftlv';
        updateTopbarUploadButton();
    }

    async function clearPlayerScreen() {
        await clearServerScreenState('player');
        try { playerExternalFiles = []; } catch {}

        stopPlayer();

        const playerSelect = document.getElementById('playerFileSelect');
        if (playerSelect) playerSelect.innerHTML = '';
        const playerFrame = document.getElementById('playerFrame');
        if (playerFrame) playerFrame.removeAttribute('src');
        const playerSeek = document.getElementById('playerSeek');
        if (playerSeek) {
            playerSeek.max = '0';
            playerSeek.value = '0';
        }
        const playerAudio = document.getElementById('playerAudio');
        if (playerAudio) {
            try { playerAudio.pause(); } catch {}
            playerAudio.removeAttribute('src');
        }
        const playerFrameLabel = document.getElementById('playerFrameLabel');
        if (playerFrameLabel) playerFrameLabel.textContent = 'Frame: - / -';
        const playerMetaLabel = document.getElementById('playerMetaLabel');
        if (playerMetaLabel) playerMetaLabel.textContent = 'FPS: -';
        showPlayerNotice('');

        playerCurrentFile = null;
        playerInfo = null;
        playerFrameIndex = 0;
        playerPlaying = false;
    }

    async function clearCurrentScreen() {
        const tab = getActiveTabName();
        try {
            if (tab === 'playlist') {
                await clearPlaylistScreen();
            } else if (tab === 'filegen') {
                await clearFileGenScreen();
            } else if (tab === 'mp4ftlv') {
                await clearMp4FtlvScreen();
            } else if (tab === 'player') {
                await clearPlayerScreen();
            } else {
                showToast('Nothing to clear on this tab');
                return;
            }
        } catch (e) {
            alert('Clear screen failed: ' + e);
            return;
        }

        showToast('Current tab cleared');
    }

    function toggleFileGenSettings() {
        const panel = document.getElementById('fileGenSettingsPanel');
        const button = document.getElementById('btnFileGenSettings');
        if (!panel || !button) return;
        const isOpen = panel.classList.toggle('open');
        button.textContent = isOpen ? 'Hide Settings' : 'Settings';
    }

    function togglePlaylistAdvanced() {
        const panel = document.getElementById('playlistAdvancedPanel');
        const btn = document.getElementById('btnPlaylistAdvanced');
        if (!panel || !btn) return;
        const isOpen = panel.classList.toggle('open');
        btn.innerHTML = `<img class="icon" src="/static/icons/tune.svg" alt="">${isOpen ? 'Hide Advanced' : 'Advanced'}`;
    }

    function toggleHistoryPanel(bodyId, buttonId, label) {
        const body = document.getElementById(bodyId);
        const button = document.getElementById(buttonId);
        if (!body || !button) return;
        const isOpen = body.classList.toggle('open');
        button.textContent = isOpen ? `Hide ${label}` : `Open ${label}`;
    }

    function setHistoryPanelState(bodyId, buttonId, label, isOpen) {
        const body = document.getElementById(bodyId);
        const button = document.getElementById(buttonId);
        if (!body || !button) return;
        body.classList.toggle('open', !!isOpen);
        button.textContent = isOpen ? `Hide ${label}` : `Open ${label}`;
    }

    function isFileGenHistoryMode() {
        return document.getElementById('sectionFileGen')?.dataset.historyMode === 'open';
    }

    function setFileGenHistoryMode(isOpen) {
        const section = document.getElementById('sectionFileGen');
        const workspaceCard = document.getElementById('fileGenWorkspaceCard');
        const historyCard = document.getElementById('historyCard');
        const btn = document.getElementById('btnFileGenHistoryMode');
        const backBtn = document.getElementById('btnFileGenHistoryBack');
        if (!section || !workspaceCard || !historyCard || !btn) return;

        section.dataset.historyMode = isOpen ? 'open' : 'closed';
        workspaceCard.style.display = isOpen ? 'none' : 'block';
        historyCard.style.display = 'block';
        const historyBody = document.getElementById('historyCardBody');
        if (historyBody) historyBody.classList.add('open');
        btn.textContent = isOpen ? 'Back to Upload' : 'History';
        if (backBtn) backBtn.style.display = isOpen ? 'inline-flex' : 'none';
        updateTopbarUploadButton();
    }

    function toggleFileGenHistoryMode() {
        setFileGenHistoryMode(!isFileGenHistoryMode());
    }

    function syncSelection(files) {
        const existing = new Set(files.map(f => f.fileName));
        for (const name of Array.from(selectedFiles)) {
            if (!existing.has(name)) selectedFiles.delete(name);
        }
    }

    function syncGeneratorMaxEntries(files) {
        const headerStyle = document.getElementById('genHeaderStyle')?.value || 'count_fc';
        const maxInput = document.getElementById('genMaxEntries');
        if (!maxInput) return;

        if (headerStyle === 'count_fc') {
            const enabledCount = files.filter(f => (f.enabled === true) && !f.parseError).length;
            maxInput.value = enabledCount;
            maxInput.disabled = true;
            maxInput.title = 'count_fc uses all enabled files automatically';
        } else {
            maxInput.disabled = false;
            maxInput.title = '';
        }
    }

    function getOrderedPlaylistFiles(files) {
        const list = Array.isArray(files) ? files : [];
        const fileMap = new Map(list.map(f => [f.fileName, f]));
        const order = [];
        const seen = new Set();
        for (const name of (window.playlistOrder || [])) {
            if (fileMap.has(name) && !seen.has(name)) {
                seen.add(name);
                order.push(fileMap.get(name));
            }
        }
        for (const file of list) {
            if (file && file.fileName && !seen.has(file.fileName)) {
                seen.add(file.fileName);
                order.push(file);
            }
        }
        return order;
    }

    async function savePlaylistOrder(order, opts = { quiet: false }) {
        const res = await fetch('/api/generator', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ playlist_order: order })
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
            alert(body.error || 'Failed to save playlist order');
            return false;
        }
        window.playlistOrder = Array.isArray(body.playlist_order) ? body.playlist_order.slice() : order.slice();
        if (!opts?.quiet) showToast('Playlist order updated');
        return true;
    }

    function renderPlaylistOrder(files) {
        const host = document.getElementById('playlistOrderList');
        if (!host) return;

        const orderedFiles = getOrderedPlaylistFiles(files);
        window.playlistOrder = orderedFiles.map(f => f.fileName);
        const selectedCount = selectedFiles.size;

        if (orderedFiles.length === 0) {
            host.innerHTML = '<div class="notice" style="margin:0;">No playlist files available yet.</div>';
            return;
        }

        const toolbar = `
            <div class="order-toolbar">
                <div style="color: var(--text-dim); font-size: 0.9rem;">
                    Selected: <strong style="color: var(--text);">${selectedCount}</strong>
                    <span style="margin-left: 0.75rem;">Showing <strong style="color: var(--text);">${orderedFiles.length}</strong> file(s)</span>
                </div>
                <div style="display: flex; gap: 0.5rem; flex-wrap: wrap; justify-content: flex-end;">
                    <button class="btn-primary" onclick="selectAllVisible()">Select all</button>
                    <button class="btn-primary" onclick="clearSelection()">Clear</button>
                    <button class="btn-danger" onclick="deleteSelected()" ${selectedCount === 0 ? 'disabled style="opacity:0.5; cursor:not-allowed;"' : ''}>Remove selected</button>
                    <button class="btn-danger" onclick="deleteAll()">Remove all</button>
                </div>
            </div>
        `;

        host.innerHTML = toolbar + orderedFiles.map((file, index) => {
            const fileLit = JSON.stringify(String(file.fileName || ''));
            return `
                <div
                    class="order-item"
                    draggable="true"
                    data-file-name="${escHtml(file.fileName)}"
                    ondragstart='onPlaylistDragStart(event, ${fileLit})'
                    ondragend="onPlaylistDragEnd(event)"
                    ondragover="onPlaylistDragOver(event)"
                    ondragleave="onPlaylistDragLeave(event)"
                    ondrop='onPlaylistDrop(event, ${fileLit})'
                    style="${file.enabled ? '' : 'opacity:0.7;'}"
                >
                    <div style="display:flex; gap:0.75rem; align-items:flex-start; min-width:0; flex:1;">
                        <input type="checkbox" ${selectedFiles.has(file.fileName) ? 'checked' : ''} onchange='toggleSelect(${fileLit}, this.checked)'>
                        <span class="order-index">${index + 1}</span>
                        <div style="min-width:0; flex:1;">
                            <div style="display:flex; gap:0.6rem; align-items:center; flex-wrap:wrap;">
                                <div style="font-weight:600; word-break:break-word;">${escHtml(file.fileName)}</div>
                                <span class="status-badge ${file.parseError ? 'badge-error' : (file.willBeIncluded ? 'badge-included' : 'badge-skipped')}">
                                    ${file.parseError ? 'INVALID' : (file.willBeIncluded ? 'INCLUDED' : 'SKIPPED')}
                                </span>
                            </div>
                            <div style="font-size:0.78rem; color: var(--text-dim); margin-top:0.2rem;">
                                ${file.parseError ? ('Error: ' + escHtml(file.parseError)) : ('v1=' + (file.v1 ?? 0) + ', v2=' + (file.v2 ?? 0) + ', header=' + (file.headerStyle ?? 'count_fc') + ':' + (file.headerUsedSlots ?? 0))}
                            </div>
                        </div>
                    </div>
                    <div class="order-controls">
                        <div class="order-inline-field">
                            <label>CRC (Hex8)</label>
                            <input type="text" value="${file.crc ?? ''}" maxlength="8" onchange='updateSetting(${fileLit}, "crc", this.value)'>
                        </div>
                        <div class="order-inline-field" style="min-width:85px;">
                            <label>Enabled</label>
                            <input type="checkbox" ${file.enabled ? 'checked' : ''} onchange='updateSetting(${fileLit}, "enabled", this.checked)'>
                        </div>
                        <div>
                            <button class="btn-danger" onclick='deleteFile(${fileLit})'>Remove</button>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }

    function onPlaylistDragStart(event, fileName) {
        playlistDragFile = fileName;
        const item = event.currentTarget;
        if (item) item.classList.add('dragging');
        if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', fileName);
        }
    }

    function onPlaylistDragEnd(event) {
        playlistDragFile = null;
        document.querySelectorAll('.order-item').forEach(el => {
            el.classList.remove('dragging');
            el.classList.remove('drop-target');
        });
    }

    function onPlaylistDragOver(event) {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
        const item = event.currentTarget;
        if (item) item.classList.add('drop-target');
    }

    function onPlaylistDragLeave(event) {
        const item = event.currentTarget;
        if (item) item.classList.remove('drop-target');
    }

    async function onPlaylistDrop(event, targetFileName) {
        event.preventDefault();
        const item = event.currentTarget;
        if (item) item.classList.remove('drop-target');
        const dragged = playlistDragFile || (event.dataTransfer ? event.dataTransfer.getData('text/plain') : '');
        if (!dragged || !targetFileName || dragged === targetFileName) return;

        const orderedFiles = getOrderedPlaylistFiles(window.playlistLastFiles || []);
        const names = orderedFiles.map(f => f.fileName);
        const from = names.indexOf(dragged);
        const to = names.indexOf(targetFileName);
        if (from < 0 || to < 0) return;

        names.splice(to, 0, names.splice(from, 1)[0]);
        window.playlistOrder = names.slice();

        const reordered = getOrderedPlaylistFiles(window.playlistLastFiles || []);
        window.playlistLastFiles = reordered;
        renderPlaylistOrder(reordered);
        renderFileList(reordered);
        await savePlaylistOrder(names, { quiet: true });
    }

    async function resetPlaylistOrder() {
        window.playlistOrder = [];
        const ok = await savePlaylistOrder([], { quiet: false });
        if (!ok) return;
        await loadFiles();
    }

    function renderFileList(files) {
        const container = document.getElementById('fileList');
        if (!container) return;
        container.style.display = 'none';
        container.innerHTML = '';
        return;
        if (files.length === 0) {
            container.innerHTML = '<p style="text-align:center; color: var(--text-dim);">No files in folder.</p>';
            return;
        }

        const total = files.length;
        const pageSize = Math.max(5, parseInt(window.playlistPageSize || 20, 10) || 20);
        const totalPages = Math.max(1, Math.ceil(total / pageSize));
        let page = Math.max(1, parseInt(window.playlistPage || 1, 10) || 1);
        if (page > totalPages) page = totalPages;
        window.playlistPage = page;
        window.playlistPageSize = pageSize;

        const start = (page - 1) * pageSize;
        const end = Math.min(total, start + pageSize);
        const pageFiles = files.slice(start, end);

        const makePageButtons = () => {
            if (totalPages <= 1) return '';
            const btn = (p, label, active = false) =>
                `<button class="btn-secondary ${active ? 'active' : ''}" style="padding:0.35rem 0.6rem; ${active ? 'border-color: var(--primary); color: var(--primary);' : ''}" onclick="setPlaylistPage(${p})">${label}</button>`;

            const parts = [];
            parts.push(btn(Math.max(1, page - 1), '‹', false));

            const windowSize = 7;
            let left = Math.max(1, page - Math.floor(windowSize / 2));
            let right = Math.min(totalPages, left + windowSize - 1);
            left = Math.max(1, right - windowSize + 1);

            if (left > 1) {
                parts.push(btn(1, '1', page === 1));
                if (left > 2) parts.push(`<span style="color: var(--text-dim); padding: 0 0.25rem;">…</span>`);
            }

            for (let p = left; p <= right; p++) {
                parts.push(btn(p, String(p), p === page));
            }

            if (right < totalPages) {
                if (right < totalPages - 1) parts.push(`<span style="color: var(--text-dim); padding: 0 0.25rem;">…</span>`);
                parts.push(btn(totalPages, String(totalPages), page === totalPages));
            }

            parts.push(btn(Math.min(totalPages, page + 1), '›', false));
            return parts.join('');
        };

        const selectedCount = selectedFiles.size;
        const bar = `
            <div style="grid-column: 1 / -1; display: flex; justify-content: space-between; align-items: center; gap: 1rem; padding: 0.75rem 0.9rem; border: 1px solid var(--border); border-radius: 12px; background: rgba(255,255,255,0.02);">
                <div style="color: var(--text-dim); font-size: 0.9rem;">
                    Selected: <strong style="color: var(--text);">${selectedCount}</strong>
                    <span style="margin-left: 0.75rem;">Showing <strong style="color: var(--text);">${start + 1}-${end}</strong> of <strong style="color: var(--text);">${total}</strong></span>
                </div>
                <div style="display: flex; gap: 0.5rem; flex-wrap: wrap; justify-content: flex-end;">
                    <select id="playlistPageSize" onchange="setPlaylistPageSize(this.value)" style="padding: 0.35rem 0.5rem; border-radius: 8px;">
                        <option value="10" ${pageSize === 10 ? 'selected' : ''}>10/page</option>
                        <option value="20" ${pageSize === 20 ? 'selected' : ''}>20/page</option>
                        <option value="50" ${pageSize === 50 ? 'selected' : ''}>50/page</option>
                        <option value="100" ${pageSize === 100 ? 'selected' : ''}>100/page</option>
                    </select>
                    <button class="btn-primary" onclick="selectAllVisible()">Select all</button>
                    <button class="btn-primary" onclick="clearSelection()">Clear</button>
                    <button class="btn-danger" onclick="deleteSelected()" ${selectedCount === 0 ? 'disabled style=\"opacity:0.5; cursor:not-allowed;\"' : ''}>Remove selected</button>
                    <button class="btn-danger" onclick="deleteAll()">Remove all</button>
                </div>
            </div>
            <div style="grid-column: 1 / -1; display:flex; justify-content:center; align-items:center; gap: 0.35rem; padding: 0.6rem 0.4rem;">
                ${makePageButtons()}
            </div>
        `;

        container.innerHTML = pageFiles.map(file => `
            <div class="file-item">
                <div class="file-info">
                    <div style="display:flex; align-items:center; gap: 0.75rem;">
                        <input type="checkbox" ${selectedFiles.has(file.fileName) ? 'checked' : ''} onchange="toggleSelect('${file.fileName}', this.checked)">
                        <h4 style="margin:0;">#${file.orderIndex ?? '?'} ${file.fileName}</h4>
                    </div>
                    <span class="status-badge ${file.parseError ? 'badge-error' : (file.willBeIncluded ? 'badge-included' : 'badge-skipped')}">
                        ${file.parseError ? 'INVALID' : (file.willBeIncluded ? 'INCLUDED' : 'SKIPPED')}
                    </span>
                    <p style="margin-top: 0.4rem;">
                        ${file.parseError ? ('Error: ' + file.parseError) : ('v1=' + (file.v1 ?? 0) + ', v2=' + (file.v2 ?? 0) + ', max=' + (file.maxEntries ?? 0) + ', header=' + (file.headerStyle ?? 'count_fc') + ':' + (file.headerUsedSlots ?? 0))}
                    </p>
                </div>
                <div class="input-group">
                    <label>V1</label>
                    <input type="number" value="${file.v1 ?? 0}" disabled>
                </div>
                <div class="input-group">
                    <label>V2</label>
                    <input type="number" value="${file.v2 ?? 0}" disabled>
                </div>
                <div class="input-group">
                    <label>CRC (Hex8)</label>
                    <input type="text" value="${file.crc ?? ''}" maxlength="8" onchange="updateSetting('${file.fileName}', 'crc', this.value)">
                </div>
                <div class="input-group">
                    <label>Enabled</label>
                    <input type="checkbox" ${file.enabled ? 'checked' : ''} onchange="updateSetting('${file.fileName}', 'enabled', this.checked)">
                </div>
                <div>
                    <button class="btn-danger" onclick="deleteFile('${file.fileName}')">Remove</button>
                </div>
            </div>
        `).join('');

        container.innerHTML = bar + container.innerHTML;
    }

    function setPlaylistPage(p) {
        window.playlistPage = Math.max(1, parseInt(p || 1, 10) || 1);
    }

    function setPlaylistPageSize(sz) {
        window.playlistPageSize = Math.max(5, parseInt(sz || 20, 10) || 20);
        window.playlistPage = 1;
    }

    function toggleSelect(fileName, checked) {
        if (checked) selectedFiles.add(fileName);
        else selectedFiles.delete(fileName);
        renderPlaylistOrder(window.playlistLastFiles || []);
    }

    function selectAllVisible() {
        const files = window.playlistLastFiles || [];
        files.forEach(f => {
            if (f && f.fileName) selectedFiles.add(f.fileName);
        });
        renderPlaylistOrder(files);
    }

    function clearSelection() {
        selectedFiles.clear();
        renderPlaylistOrder(window.playlistLastFiles || []);
    }

    async function deleteSelected() {
        const names = Array.from(selectedFiles);
        if (names.length === 0) return;
        if (!confirm(`Remove ${names.length} selected file(s) from folder?`)) return;
        const res = await fetch('/api/delete_many', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ fileNames: names })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert(err.error || 'Delete failed');
            return;
        }
        selectedFiles.clear();
        loadFiles();
    }

    async function deleteAll() {
        if (!confirm('Remove ALL files from folder?')) return;
        const res = await fetch('/api/delete_all', { method: 'POST' });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert(err.error || 'Delete all failed');
            return;
        }
        selectedFiles.clear();
        loadFiles();
    }

    async function updateSetting(fileName, key, value) {
        const payload = { fileName };
        payload[key] = value;
        const res = await fetch('/api/update', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert(err.error || 'Update failed');
            return;
        }
        loadFiles();
    }

    // (Legacy) Drag & drop upload zone for Playlist Manager was removed.
    // Keep handlers guarded to avoid runtime errors when the element is not present.
    const dropZone = document.getElementById('dropZone');
    if (dropZone) {
        // Standard listeners for drag states
        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            dropZone.addEventListener(eventName, preventDefaults, false);
            document.body.addEventListener(eventName, preventDefaults, false);
        });

    function preventDefaults (e) {
        e.preventDefault();
        e.stopPropagation();
    }

        ['dragenter', 'dragover'].forEach(eventName => {
            dropZone.addEventListener(eventName, () => {
                dropZone.style.borderColor = 'var(--primary)';
                dropZone.style.background = 'rgba(59, 130, 246, 0.1)';
            }, false);
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropZone.addEventListener(eventName, () => {
                dropZone.style.borderColor = 'var(--border)';
                dropZone.style.background = 'rgba(255,255,255,0.02)';
            }, false);
        });

        dropZone.addEventListener('drop', handleDrop, false);
    }

    function handleDrop(e) {
        let dt = e.dataTransfer;
        let files = dt.files;
        if (files.length > 0) {
            uploadFiles(files);
        }
    }

    async function handleUpload(e) {
        const files = e.target.files;
        if (files.length > 0) {
            await uploadFiles(files);
        }
    }

    async function uploadFiles(files) {
        const formData = new FormData();
        for (let i = 0; i < files.length; i++) {
            formData.append('files', files[i]);
        }

        try {
            const res = await fetch('/api/upload', {
                method: 'POST',
                body: formData
            });

            if (res.ok) {
                markPlaylistStorageSelected();
                loadFiles();
            } else {
                alert("Upload failed. Check if the server is running.");
            }
        } catch (err) {
            console.error(err);
            alert("Error connecting to server.");
        }
    }

    async function deleteFile(fileName) {
        if (!confirm(`Remove ${fileName} from playlist and folder?`)) return;
        await fetch('/api/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ fileName })
        });
        loadFiles();
    }

    function showToast(msg, isError = false, kind = '') {
        const t = document.getElementById('toast');
        const icon = t ? t.querySelector('.toast-icon') : null;
        document.getElementById('toastMsg').innerText = msg;
        const resolved = (kind || (isError ? 'error' : 'success')).toLowerCase();
        if (t) {
            t.classList.toggle('error', resolved === 'error');
            t.classList.toggle('neutral', resolved === 'neutral');
        }
        if (icon) icon.textContent = resolved === 'error' ? '!' : (resolved === 'neutral' ? '×' : '✓');
        t.classList.add('visible');
        setTimeout(() => t.classList.remove('visible'), 3000);
    }

    function isPlaylistStorageSelected() {
        // Per-session: avoid sticky old folders across reloads.
        return sessionStorage.getItem('playlist_storage_selected') === '1';
    }

    function markPlaylistStorageSelected() {
        sessionStorage.setItem('playlist_storage_selected', '1');
    }

    function escHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, (c) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[c]));
    }

    function formatTaskError(result) {
        if (!result || result.ok) return '';
        const input = result.input ? `File: ${escHtml(result.input)}` : 'File failed';
        const error = escHtml(result.error || 'Conversion failed');
        const details = result.details ? `<div class="task-error-details">${escHtml(result.details)}</div>` : '';
        return `
            <div class="task-error-box">
                <div class="task-error-title">${input}</div>
                <div>${error}</div>
                ${details}
            </div>
        `;
    }

    function formatTaskTitle(task) {
        const files = (task.files || []).map(f => f && f.original).filter(Boolean);
        if (files.length === 0) return 'Untitled Conversion';
        if (files.length === 1) return files[0];
        return `${files[0]} +${files.length - 1} more`;
    }

    async function playHistoryPath(path, fileName) {
        try {
            if (!path) {
                alert('Missing file path');
                return;
            }

            const res = await fetch('/api/player_token', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ path })
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                alert(data.error || 'Failed to open file in player');
                return;
            }

            playerExternalFiles = playerExternalFiles || [];
            playerExternalFiles = playerExternalFiles.filter(x => x.token !== data.token);
            playerExternalFiles.unshift({ token: data.token, fileName: data.fileName || fileName || 'FTLV' });

            switchTab('player');
            await loadPlayableFiles();
            const sel = document.getElementById('playerFileSelect');
            if (sel) {
                sel.value = `token:${data.token}`;
                onPlayerFileChange();
            }
        } catch (e) {
            alert('Play failed: ' + e);
        }
    }

    async function generateAndDownload() {
        const btn = document.querySelector('.btn-generate');
        const oldText = btn.textContent;
        btn.disabled = true;
        btn.textContent = "Updating FTL.LIS...";

        try {
            const res = await fetch('/api/generate', { method: 'POST' });
            const data = await res.json();
            if (res.ok) {
                showToast(data.message || "FTL.LIS Generated successfully!");
                loadFiles(); // Refresh the list
            } else {
                alert(data.error || "Failed to generate playlist");
            }
        } catch (err) {
            alert("Error connecting to server");
        } finally {
            btn.disabled = false;
            btn.textContent = oldText;
        }
    }


    // Tab Switching
    function updateWorkspaceHeader(tab) {
        const title = document.getElementById('workspaceTitle');
        const subtitle = document.getElementById('workspaceSubtitle');
        if (!title || !subtitle) return;

        const copy = {
            filegen: {
                title: 'Media File Generator',
                subtitle: 'Convert MP4 and image assets into fan-playable FTLV files.'
            },
            mp4ftlv: {
                title: '.mp4_to_.ftlv',
                subtitle: 'Create direct MP4-to-FTLV outputs in a separate focused workflow.'
            },
            playlist: {
                title: 'Playlist Manager',
                subtitle: 'Organize media order, merge with FTL.LIS, and control final playlist output.'
            },
            player: {
                title: 'FTLV Player',
                subtitle: 'Preview generated hologram files on desktop before copying them to the device.'
            },
            config: {
                title: 'Device Settings',
                subtitle: 'Manage the fan device identity, Wi-Fi configuration, and shared system settings.'
            }
        };

        const content = copy[tab] || copy.filegen;
        title.textContent = content.title;
        subtitle.textContent = content.subtitle;
    }

    function switchTab(tab) {
        document.getElementById('sectionPlaylist').style.display = tab === 'playlist' ? 'block' : 'none';
        document.getElementById('sectionFileGen').style.display = tab === 'filegen' ? 'block' : 'none';
        document.getElementById('sectionMp4ToFtlv').style.display = tab === 'mp4ftlv' ? 'block' : 'none';
        document.getElementById('sectionPlayer').style.display = tab === 'player' ? 'block' : 'none';
        document.getElementById('sectionConfig').style.display = tab === 'config' ? 'block' : 'none';
        
        document.getElementById('tabPlaylist').classList.toggle('active', tab === 'playlist');
        document.getElementById('tabFileGen').classList.toggle('active', tab === 'filegen');
        document.getElementById('tabMp4ToFtlv').classList.toggle('active', tab === 'mp4ftlv');
        document.getElementById('tabPlayer').classList.toggle('active', tab === 'player');
        document.getElementById('tabConfig').classList.toggle('active', tab === 'config');
        updateWorkspaceHeader(tab);
        if (tab !== 'filegen' && isFileGenHistoryMode()) {
            setFileGenHistoryMode(false);
        }
        updateTopbarUploadButton();

        if (tab === 'playlist') {
            loadPlaylistWorkspace();
        }
        if (tab === 'config') loadConfig();
        if (tab === 'filegen') {
            loadTasks();
            autoRefreshTasks(loadTasks);
        } else if (tab === 'mp4ftlv') {
            loadMp4FtlvTasks();
            autoRefreshTasks(loadMp4FtlvTasks);
        } else {
            stopAutoRefresh();
        }

        if (tab === 'player') {
            stopAutoRefresh();
            loadPlayableFiles();
            setPlayerPlaceholderVisible(true);
        } else {
            stopPlayer();
        }
    }

    let pendingMediaFiles = [];
    let mp4FtlvPendingFiles = [];
    let pendingFolderName = null;
    let mp4FtlvPendingFolderName = null;

    function handleMediaSelect(e) {
        const files = Array.from(e.target.files || []);
        e.target.value = '';
        if (files.length === 0) return;
        
        pendingFolderName = null;
        pendingMediaFiles = [...pendingMediaFiles, ...files];
        renderSelectionList();
    }

    function _topFolderNameFromWebkitPath(files) {
        for (const f of (files || [])) {
            const p = f && (f.webkitRelativePath || '');
            if (p && p.includes('/')) return p.split('/')[0] || null;
        }
        return null;
    }

    function handleMediaFolderSelect(e) {
        const files = Array.from(e.target.files || []);
        e.target.value = '';
        if (files.length === 0) return;
        pendingFolderName = _topFolderNameFromWebkitPath(files);
        pendingMediaFiles = [...pendingMediaFiles, ...files];
        renderSelectionList();
    }

    function renderSelectionList() {
        const area = document.getElementById('selectionArea');
        const list = document.getElementById('selectedFilesList');
        const btnTop = document.getElementById('btnStartConversionTop');
        const topbar = document.getElementById('fileGenLoadedBar');
        const title = document.getElementById('fileGenLoadedTitle');
        const dropZone = document.getElementById('genDropZone');
        const toggle = document.getElementById('fileGenFtlvExtToggle');
        
        if (pendingMediaFiles.length === 0) {
            area.style.display = 'none';
            if (topbar) topbar.classList.remove('open');
            if (dropZone) dropZone.style.display = 'block';
            updateTopbarUploadButton();
            return;
        }

        area.style.display = 'block';
        if (topbar) topbar.classList.add('open');
        if (dropZone) dropZone.style.display = 'none';
        if (title) title.textContent = pendingMediaFiles.length === 1
            ? `1 file loaded: ${pendingMediaFiles[0].name}`
            : `${pendingMediaFiles.length} files loaded`;
        list.innerHTML = pendingMediaFiles.map((f, i) => `
            <div class="task-item" style="padding: 0.75rem;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 0.9rem;">${f.name} (${(f.size/1024/1024).toFixed(2)} MB)</span>
                    <button class="btn-danger" style="padding: 0.2rem 0.5rem; font-size: 0.7rem;" onclick="removePending(${i})">Remove</button>
                </div>
            </div>
        `).join('');

        const usingFtlvExt = !!toggle?.checked;
        if (btnTop) btnTop.textContent = usingFtlvExt
            ? `Generate .ftlv (${pendingMediaFiles.length})`
            : `Start Conversion (${pendingMediaFiles.length})`;
        updateTopbarUploadButton();
    }

    function removePending(index) {
        pendingMediaFiles.splice(index, 1);
        renderSelectionList();
    }

    async function startConversion() {
        if (pendingMediaFiles.length === 0) return;

        const existing = (document.getElementById('fileGenTargetDir')?.value || '').trim();
        openOutputDirModal({
            title: 'Choose Target Folder',
            subtitle: 'Select where converted files should be saved.',
            defaultDir: existing,
            onCancel: () => showToast('Cancelled', false, 'neutral'),
            onSubmit: (dir) => { startConversionWithOutputDir(dir); }
        });
    }

    async function startConversionWithOutputDir(outputDir) {
        const btn = document.getElementById('btnStartConversion');
        const btnTop = document.getElementById('btnStartConversionTop');
        const useFtlvExt = !!document.getElementById('fileGenFtlvExtToggle')?.checked;
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Uploading...";
        }
        if (btnTop) {
            btnTop.disabled = true;
            btnTop.textContent = 'Uploading...';
        }

        const formData = new FormData();
        pendingMediaFiles.forEach(f => formData.append('file', f));
        formData.append('quality', document.getElementById('convQuality').value);
        formData.append('fps', document.getElementById('convFps').value);
        formData.append('outputDir', (outputDir || '').trim());

        // Keep the visible input in sync (but we still prompt every time).
        const fgTarget = document.getElementById('fileGenTargetDir');
        if (fgTarget) fgTarget.value = (outputDir || '').trim();

        try {
            let endpoint = '/api/convert_media_async';
            if (useFtlvExt) {
                const nonMp4 = pendingMediaFiles.filter(f => !/\.mp4$/i.test(f?.name || ''));
                if (nonMp4.length) {
                    alert('When ".ftlv extension" is enabled, only MP4 files are allowed.');
                    showToast('Only MP4 allowed in .ftlv extension mode', true);
                    return;
                }
                if (pendingFolderName) formData.append('folderName', pendingFolderName);
                endpoint = '/api/convert_mp4_ftlv_async';
            }
            if (!useFtlvExt && pendingFolderName) formData.append('folderName', pendingFolderName);

            const res = await fetch(endpoint, { method: 'POST', body: formData });
            const data = await res.json();
            
            if (res.ok) {
                if (isFileGenHistoryMode()) setFileGenHistoryMode(false);
                pendingFolderName = null;
                pendingMediaFiles = [];
                renderSelectionList();
                document.getElementById('historyCard').style.display = 'block';
                document.getElementById('historyCardBody')?.classList.add('open');
                autoRefreshTasks(loadTasks);
                loadTasks();
                const fg = document.getElementById('fileGenTargetDir');
                const saved = document.getElementById('fileGenSavedPath');
                if (saved) {
                    const td = (fg && fg.value.trim()) ? fg.value.trim() : '(default folder)';
                    saved.textContent = `Converting... output will be saved in: ${td}`;
                }
            } else {
                const message = [data.error, data.details].filter(Boolean).join('\n');
                alert(message || "Failed to start conversion");
                showToast(data.error || 'Conversion could not start', true);
            }
        } catch (err) {
            alert("Error connecting to server");
            showToast('Could not connect to the conversion server', true);
        } finally {
            if (btn) btn.disabled = false;
            if (btnTop) btnTop.disabled = false;
            renderSelectionList();
        }
    }

    function onFileGenFtlvToggleChange() {
        const toggle = document.getElementById('fileGenFtlvExtToggle');
        const mediaInput = document.getElementById('mediaInput');
        const hint = document.getElementById('fileGenDropZoneHint');
        const useFtlvExt = !!toggle?.checked;
        if (mediaInput) {
            mediaInput.accept = useFtlvExt ? 'video/mp4,.mp4' : 'image/*,video/mp4';
        }
        if (hint) {
            hint.textContent = useFtlvExt
                ? 'MP4 only. Output files will end with .ftlv'
                : 'Supports MP4, JPG, PNG, GIF';
        }
        if (useFtlvExt) {
            const before = pendingMediaFiles.length;
            pendingMediaFiles = pendingMediaFiles.filter(f => /\.mp4$/i.test(f?.name || ''));
            if (pendingMediaFiles.length !== before) {
                showToast('Non-MP4 files removed (FTLV extension mode)', true);
            }
        }
        renderSelectionList();
    }

    function handleMp4FtlvSelect(e) {
        const files = Array.from(e.target.files || []).filter(f => /\.mp4$/i.test(f.name || ''));
        e.target.value = '';
        if (files.length === 0) return;
        mp4FtlvPendingFolderName = null;
        mp4FtlvPendingFiles = [...mp4FtlvPendingFiles, ...files];
        renderMp4FtlvSelectionList();
    }

    function handleMp4FtlvFolderSelect(e) {
        const files = Array.from(e.target.files || []).filter(f => /\.mp4$/i.test(f.name || ''));
        e.target.value = '';
        if (files.length === 0) return;
        mp4FtlvPendingFolderName = _topFolderNameFromWebkitPath(files);
        mp4FtlvPendingFiles = [...mp4FtlvPendingFiles, ...files];
        renderMp4FtlvSelectionList();
    }

    function renderMp4FtlvSelectionList() {
        const area = document.getElementById('mp4FtlvSelectionArea');
        const list = document.getElementById('mp4FtlvSelectedFilesList');
        const btnTop = document.getElementById('btnStartMp4FtlvConversionTop');
        const topbar = document.getElementById('mp4FtlvLoadedBar');
        const title = document.getElementById('mp4FtlvLoadedTitle');
        const dropZone = document.getElementById('mp4FtlvDropZone');
        if (!area || !list) return;

        if (mp4FtlvPendingFiles.length === 0) {
            area.style.display = 'none';
            if (topbar) topbar.classList.remove('open');
            if (dropZone) dropZone.style.display = 'block';
            updateTopbarUploadButton();
            return;
        }

        area.style.display = 'block';
        if (topbar) topbar.classList.add('open');
        if (dropZone) dropZone.style.display = 'none';
        if (title) title.textContent = mp4FtlvPendingFiles.length === 1
            ? `1 MP4 loaded: ${mp4FtlvPendingFiles[0].name}`
            : `${mp4FtlvPendingFiles.length} MP4 files loaded`;
        list.innerHTML = mp4FtlvPendingFiles.map((f, i) => `
            <div class="task-item" style="padding: 0.75rem;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 0.9rem;">${escHtml(f.name)} (${(f.size / 1024 / 1024).toFixed(2)} MB)</span>
                    <button class="btn-danger" style="padding: 0.2rem 0.5rem; font-size: 0.7rem;" onclick="removePendingMp4Ftlv(${i})">Remove</button>
                </div>
            </div>
        `).join('');

        if (btnTop) btnTop.textContent = `Generate .ftlv (${mp4FtlvPendingFiles.length})`;
        updateTopbarUploadButton();
    }

    function removePendingMp4Ftlv(index) {
        mp4FtlvPendingFiles.splice(index, 1);
        renderMp4FtlvSelectionList();
    }

    async function browseMp4FtlvTargetDir() {
        try {
            const res = await fetch('/api/browse?mode=default_output');
            const data = await res.json();
            if (data.path) {
                const input = document.getElementById('mp4FtlvTargetDir');
                if (input) input.value = data.path;
                await saveMp4FtlvOutputDir({ quiet: true });
            } else if (data.error) {
                alert('Browse failed: ' + data.error);
            }
        } catch (e) {
            alert('Browse failed: ' + e);
        }
    }

    async function saveMp4FtlvOutputDir(opts = { quiet: false }) {
        const input = document.getElementById('mp4FtlvTargetDir');
        if (!input) return;
        const val = input.value.trim();
        const res = await fetch('/api/generator', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ mp4ftlv_output_directory: val })
        });
        if (!res.ok) {
            showToast('Failed to save .mp4_to_.ftlv folder', true);
            return;
        }
        await loadGenerator();
        if (!opts?.quiet) showToast('.mp4_to_.ftlv folder saved');
    }

    async function startMp4FtlvConversion() {
        if (mp4FtlvPendingFiles.length === 0) return;

        const existing = (document.getElementById('mp4FtlvTargetDir')?.value || '').trim();
        openOutputDirModal({
            title: 'Choose Target Folder',
            subtitle: 'Select where generated .ftlv files should be saved.',
            defaultDir: existing,
            onCancel: () => showToast('Cancelled', false, 'neutral'),
            onSubmit: (dir) => { startMp4FtlvConversionWithOutputDir(dir); }
        });
    }

    async function startMp4FtlvConversionWithOutputDir(outputDir) {
        const btn = document.getElementById('btnStartMp4FtlvConversion');
        const btnTop = document.getElementById('btnStartMp4FtlvConversionTop');
        const output = (outputDir || '').trim();
        const targetInput = document.getElementById('mp4FtlvTargetDir');
        if (targetInput) targetInput.value = output;

        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Uploading...';
        }
        if (btnTop) {
            btnTop.disabled = true;
            btnTop.textContent = 'Uploading...';
        }

        const formData = new FormData();
        mp4FtlvPendingFiles.forEach(f => formData.append('file', f));
        formData.append('quality', document.getElementById('mp4FtlvQuality').value);
        formData.append('fps', document.getElementById('mp4FtlvFps').value);
        formData.append('outputDir', output);
        if (mp4FtlvPendingFolderName) formData.append('folderName', mp4FtlvPendingFolderName);

        try {
            const res = await fetch('/api/convert_mp4_ftlv_async', {
                method: 'POST',
                body: formData
            });
            const data = await res.json();
            if (res.ok) {
                mp4FtlvPendingFolderName = null;
                mp4FtlvPendingFiles = [];
                renderMp4FtlvSelectionList();
                document.getElementById('mp4FtlvHistoryCard').style.display = 'block';
                setHistoryPanelState('mp4FtlvHistoryBody', 'btnMp4HistoryToggle', '.mp4_to_.ftlv History', true);
                autoRefreshTasks(loadMp4FtlvTasks);
                loadMp4FtlvTasks();
                showToast('.ftlv generation started');
            } else {
                const message = [data.error, data.details].filter(Boolean).join('\n');
                alert(message || 'Failed to start .ftlv generation');
                showToast(data.error || 'Generation could not start', true);
            }
        } catch (err) {
            alert('Error connecting to server');
            showToast('Could not connect to the conversion server', true);
        } finally {
            if (btn) btn.disabled = false;
            if (btnTop) btnTop.disabled = false;
            renderMp4FtlvSelectionList();
        }
    }

    let taskRefreshInterval = null;
    let taskRefreshLoader = null;
    let historyFilter = 'all'; // 'all' | 'active'
    const notifiedFailedTasks = new Set();

    function toggleHistoryFilter() {
        historyFilter = historyFilter === 'active' ? 'all' : 'active';
        const btn = document.getElementById('btnHistoryFilter');
        if (btn) btn.textContent = historyFilter === 'active' ? 'Show All' : 'Show Active';
        loadTasks();
    }

    function autoRefreshTasks(loader = loadTasks) {
        if (taskRefreshInterval) return;
        taskRefreshLoader = loader;
        taskRefreshInterval = setInterval(() => {
            if (typeof taskRefreshLoader === 'function') taskRefreshLoader();
        }, 2000);
    }

    function stopAutoRefresh() {
        if (taskRefreshInterval) {
            clearInterval(taskRefreshInterval);
            taskRefreshInterval = null;
        }
        taskRefreshLoader = null;
    }

    async function loadTasks() {
        try {
            const res = await fetch(`/api/tasks?_=${Date.now()}`, { cache: 'no-store' });
            const tasks = await res.json();
            
            const list = document.getElementById('taskList');
            const card = document.getElementById('historyCard');
            const filteredTasks = (historyFilter === 'active')
                ? (tasks || []).filter(t => t && (t.status === 'pending' || t.status === 'processing'))
                : (tasks || []);

            if (filteredTasks.length > 0) {
                card.style.display = 'block';
            } else {
                if (historyFilter === 'active') {
                    card.style.display = 'block';
                    list.innerHTML = '<div class="notice history-empty">No active conversions right now.</div>';
                    return;
                }
                card.style.display = 'none';
                return;
            }

            list.innerHTML = filteredTasks.map(task => {
                const isProcessing = task.status === 'processing';
                const isDone = task.status === 'done';
                const statusColor = isDone ? 'var(--success)' : (isProcessing ? 'var(--primary)' : 'var(--text-dim)');
                const results = Array.isArray(task.results) ? task.results : [];
                const okResults = results.filter(r => r && r.ok);
                const failedResults = results.filter(r => r && !r.ok);
                const title = formatTaskTitle(task);
                const subtitle = (task.files || []).map(f => escHtml(f.original)).join(', ');
                const savedItems = okResults
                    .map(r => {
                        const p = r.outputPath || (task.targetDirectory ? `${task.targetDirectory}\\${r.output}` : r.output);
                        const n = r.output || (p ? (String(p).split(/\\\\/).pop()) : 'FTLV');
                        return { path: p, name: n };
                    })
                    .filter(x => x && x.path);
                
                return `
                    <div class="task-item">
                        <div class="task-header">
                            <span class="task-label">Task ${task.id.substring(0,8)}</span>
                            <span class="status-badge" style="background: ${statusColor}22; color: ${statusColor};">
                                ${task.status.toUpperCase()}
                            </span>
                        </div>
                        <div class="task-main">
                            <div class="task-title-row">
                                <div class="task-primary">
                                    <div class="task-primary-name">${escHtml(title)}</div>
                                    <div class="task-subtle">${subtitle}</div>
                                </div>
                            </div>
                            ${task.targetDirectory ? `
                            <div class="task-subtle">
                                Saved to: ${escHtml(task.targetDirectory)}
                            </div>
                            ` : ''}
                            ${isProcessing || isDone ? `
                            <div class="progress-container">
                                <div class="progress-bar" style="width: ${task.progress}%"></div>
                            </div>
                            <div class="task-stats">
                                <span>Progress: ${task.progress}%</span>
                                <span>${task.okCount} Done, ${task.failCount} Failed</span>
                            </div>
                            ${isDone && savedItems.length ? `
                                <div>
                                    <div class="task-label" style="margin-top: 0.2rem;">Saved Outputs</div>
                                    <div class="task-saved-list">
                                        ${savedItems.map(x => {
                                            const pLit = JSON.stringify(String(x.path || ''));
                                            const nLit = JSON.stringify(String(x.name || 'FTLV'));
                                            return `
                                                <div class="task-saved-row">
                                                    <div style="min-width: 0;">
                                                        <div style="font-weight: 600;">${escHtml(x.name)}</div>
                                                        <div class="task-subtle">${escHtml(x.path)}</div>
                                                    </div>
                                                    <button class="btn-secondary" style="padding: 6px 10px; font-size: 0.75rem;" onclick='playHistoryPath(${pLit}, ${nLit})'>▶ Play</button>
                                                </div>
                                            `;
                                        }).join('')}
                                    </div>
                                </div>
                            ` : ''}
                            ${failedResults.length ? `
                                <div>
                                    ${failedResults.map(formatTaskError).join('')}
                                </div>
                            ` : ''}
                            ` : ''}
                        </div>
                    </div>
                `;
            }).join('');

            const done = (tasks || []).find(t => t && t.status === 'done' && Array.isArray(t.results) && t.results.some(r => r && r.ok && (r.outputPath || r.output)));
            if (done) {
                const ok = done.results.filter(r => r && r.ok);
                const last = ok[ok.length - 1];
                const saved = document.getElementById('fileGenSavedPath');
                if (saved && last) {
                    const p = last.outputPath || (done.targetDirectory ? `${done.targetDirectory}\\${last.output}` : last.output);
                    if (p) saved.textContent = `Last saved: ${p}`;
                }
            }

            (tasks || []).forEach(task => {
                if (!task || task.status !== 'done' || !task.failCount || notifiedFailedTasks.has(task.id)) return;
                const failedResults = Array.isArray(task.results) ? task.results.filter(r => r && !r.ok) : [];
                const firstError = failedResults.length ? (failedResults[0].error || 'Conversion failed') : 'Conversion failed';
                showToast(`${task.failCount} file(s) failed. ${firstError}`, true);
                notifiedFailedTasks.add(task.id);
            });

            // If tasks are processing, keep refreshing. If all done, we could slow down but interval is fine.
        } catch (err) {
            console.error(err);
        }
    }

    async function loadMp4FtlvTasks() {
        try {
            const res = await fetch(`/api/tasks?_=${Date.now()}`, { cache: 'no-store' });
            const tasks = await res.json();
            const list = document.getElementById('mp4FtlvTaskList');
            const card = document.getElementById('mp4FtlvHistoryCard');
            if (!list || !card) return;

            const filtered = (tasks || []).filter(t => t && t.taskType === 'mp4_to_ftlv_ext');
            if (filtered.length === 0) {
                card.style.display = 'none';
                return;
            }
            card.style.display = 'block';

            list.innerHTML = filtered.map(task => {
                const isProcessing = task.status === 'processing';
                const isDone = task.status === 'done';
                const statusColor = isDone ? 'var(--success)' : (isProcessing ? 'var(--primary)' : 'var(--text-dim)');
                const results = Array.isArray(task.results) ? task.results : [];
                const okResults = results.filter(r => r && r.ok);
                const failedResults = results.filter(r => r && !r.ok);
                const title = formatTaskTitle(task);
                const subtitle = (task.files || []).map(f => escHtml(f.original)).join(', ');
                return `
                    <div class="task-item">
                        <div class="task-header">
                            <span class="task-label">Task ${task.id.substring(0,8)}</span>
                            <span class="status-badge" style="background: ${statusColor}22; color: ${statusColor};">${task.status.toUpperCase()}</span>
                        </div>
                        <div class="task-main">
                            <div class="task-title-row">
                                <div class="task-primary">
                                    <div class="task-primary-name">${escHtml(title)}</div>
                                    <div class="task-subtle">${subtitle}</div>
                                </div>
                            </div>
                            <div class="task-subtle">Saved to: ${escHtml(task.targetDirectory || '')}</div>
                            ${isProcessing || isDone ? `
                            <div class="progress-container">
                                <div class="progress-bar" style="width: ${task.progress}%"></div>
                            </div>
                            <div class="task-stats">
                                <span>Progress: ${task.progress}%</span>
                                <span>${task.okCount} Done, ${task.failCount} Failed</span>
                            </div>
                        ` : ''}
                        ${okResults.length ? `
                            <div>
                                <div class="task-label" style="margin-top: 0.2rem;">Saved Outputs</div>
                                <div class="task-saved-list">
                                    ${okResults.map(r => `
                                        <div class="task-saved-row">
                                            <div style="min-width: 0;">
                                                <div style="font-weight: 600;">${escHtml(r.output || 'FTLV')}</div>
                                                <div class="task-subtle">${escHtml(r.outputPath || r.output || '')}</div>
                                            </div>
                                        </div>
                                    `).join('')}
                                </div>
                            </div>
                        ` : ''}
                        ${failedResults.length ? `<div>${failedResults.map(formatTaskError).join('')}</div>` : ''}
                        </div>
                    </div>
                `;
            }).join('');
        } catch (err) {
            console.error(err);
        }
    }

    async function loadConfig() {
        try {
            const res = await fetch('/api/config');
            if (res.ok) {
                const cfg = await res.json();
                document.getElementById('cfgModel').value = cfg.model;
                document.getElementById('cfgSSID').value = cfg.ssid;
                document.getElementById('cfgPassword').value = cfg.password;
                document.getElementById('cfgDeviceName').value = cfg.deviceName;
            }
        } catch (e) { console.error("Could not load config", e); }
    }

    async function saveConfig() {
        const data = {
            ssid: document.getElementById('cfgSSID').value,
            password: document.getElementById('cfgPassword').value,
            deviceName: document.getElementById('cfgDeviceName').value
        };
        const res = await fetch('/api/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        });
        if (res.ok) alert("Device settings saved to config.ini!");
    }

    // Initial Load
    loadGenerator();
    switchTab('filegen');
    onFileGenFtlvToggleChange();
    applyTheme(getTheme());
    applyViewportMode(getViewportMode());
