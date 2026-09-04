// ================================================================
// 1. 标准图纸库（完整）
// ================================================================
const STANDARD_PAPERS = [
    { name: 'A0', w: 1189, h: 841 }, { name: 'A0_', w: 841, h: 1189 },
    { name: 'A0+1/8', w: 1338, h: 841 }, { name: 'A0+1/8_', w: 841, h: 1338 },
    { name: 'A0+1/4', w: 1486, h: 841 }, { name: 'A0+1/4_', w: 841, h: 1486 },
    { name: 'A0+3/8', w: 1635, h: 841 }, { name: 'A0+3/8_', w: 841, h: 1635 },
    { name: 'A0+1/2', w: 1783, h: 841 }, { name: 'A0+1/2_', w: 841, h: 1783 },
    { name: 'A0+5/8', w: 1932, h: 841 }, { name: 'A0+5/8_', w: 841, h: 1932 },
    { name: 'A0+3/4', w: 2080, h: 841 }, { name: 'A0+3/4_', w: 841, h: 2080 },
    { name: 'A0+7/8', w: 2230, h: 841 }, { name: 'A0+7/8_', w: 841, h: 2230 },
    { name: 'A0+1', w: 2378, h: 841 }, { name: 'A0+1_', w: 841, h: 2378 },
    { name: 'A1', w: 841, h: 594 }, { name: 'A1_', w: 594, h: 841 },
    { name: 'A1+1/4', w: 1051, h: 594 }, { name: 'A1+1/4_', w: 594, h: 1051 },
    { name: 'A1+1/2', w: 1261, h: 594 }, { name: 'A1+1/2_', w: 594, h: 1261 },
    { name: 'A1+3/4', w: 1471, h: 594 }, { name: 'A1+3/4_', w: 594, h: 1471 },
    { name: 'A1+1', w: 1682, h: 594 }, { name: 'A1+1_', w: 594, h: 1682 },
    { name: 'A1+5/4', w: 1892, h: 594 }, { name: 'A1+5/4_', w: 594, h: 1892 },
    { name: 'A1+3/2', w: 2102, h: 594 }, { name: 'A1+3/2_', w: 594, h: 2102 },
    { name: 'A1+7/4', w: 2313, h: 594 }, { name: 'A1+7/4_', w: 594, h: 2313 },
    { name: 'A1+2', w: 2523, h: 594 }, { name: 'A1+2_', w: 594, h: 2523 },
    { name: 'A1+9/4', w: 2734, h: 594 }, { name: 'A1+9/4_', w: 594, h: 2734 },
    { name: 'A1+5/2', w: 2944, h: 594 }, { name: 'A1+5/2_', w: 594, h: 2944 },
    { name: 'A2', w: 594, h: 420 }, { name: 'A2_', w: 420, h: 594 },
    { name: 'A2+1/2', w: 891, h: 420 }, { name: 'A2+1/2_', w: 420, h: 891 },
    { name: 'A2+3/4', w: 1041, h: 420 }, { name: 'A2+3/4_', w: 420, h: 1041 },
    { name: 'A2+1', w: 1189, h: 420 }, { name: 'A2+1_', w: 420, h: 1189 },
    { name: 'A2+5/4', w: 1338, h: 420 }, { name: 'A2+5/4_', w: 420, h: 1338 },
    { name: 'A2+3/2', w: 1486, h: 420 }, { name: 'A2+3/2_', w: 420, h: 1486 },
    { name: 'A2+7/4', w: 1635, h: 420 }, { name: 'A2+7/4_', w: 420, h: 1635 },
    { name: 'A2+2', w: 1783, h: 420 }, { name: 'A2+2_', w: 420, h: 1783 },
    { name: 'A2+9/4', w: 1932, h: 420 }, { name: 'A2+9/4_', w: 420, h: 1932 },
    { name: 'A2+5/2', w: 2080, h: 420 }, { name: 'A2+5/2_', w: 420, h: 2080 },
    { name: 'A3', w: 420, h: 297 }, { name: 'A3_', w: 297, h: 420 },
    { name: 'A3+1/4', w: 525, h: 297 }, { name: 'A3+1/4_', w: 297, h: 525 },
    { name: 'A3+1/2', w: 630, h: 297 }, { name: 'A3+1/2_', w: 297, h: 630 },
    { name: 'A3+3/4', w: 735, h: 297 }, { name: 'A3+3/4_', w: 297, h: 735 },
    { name: 'A3+1', w: 841, h: 297 }, { name: 'A3+1_', w: 297, h: 841 },
    { name: 'A3+3/2', w: 1051, h: 297 }, { name: 'A3+3/2_', w: 297, h: 1051 },
    { name: 'A3+2', w: 1261, h: 297 }, { name: 'A3+2_', w: 297, h: 1261 },
    { name: 'A3+5/2', w: 1471, h: 297 }, { name: 'A3+5/2_', w: 297, h: 1471 },
    { name: 'A3+3', w: 1682, h: 297 }, { name: 'A3+3_', w: 297, h: 1682 },
    { name: 'A3+7/2', w: 1892, h: 297 }, { name: 'A3+7/2_', w: 297, h: 1892 },
    { name: 'A4', w: 297, h: 210 }, { name: 'A4_', w: 210, h: 297 },
    { name: '特例 841×891', w: 891, h: 841 }, { name: '特例 841×891_', w: 841, h: 891 },
    { name: '特例 1189×1261', w: 1261, h: 1189 }, { name: '特例 1189×1261_', w: 1189, h: 1261 },
];

const BASE_PAPERS = [
    { name: 'A0', w: 1189, h: 841 }, { name: 'A0_', w: 841, h: 1189 },
    { name: 'A1', w: 841, h: 594 }, { name: 'A1_', w: 594, h: 841 },
    { name: 'A2', w: 594, h: 420 }, { name: 'A2_', w: 420, h: 594 },
    { name: 'A3', w: 420, h: 297 }, { name: 'A3_', w: 297, h: 420 },
    { name: 'A4', w: 297, h: 210 }, { name: 'A4_', w: 210, h: 297 },
];

// ================================================================
// 2. 核心判断逻辑
// ================================================================
function matchStandardExact(w, h) {
    for (const paper of STANDARD_PAPERS) {
        if (w === paper.w && h === paper.h) {
            const isLandscape = (w >= h);
            const orientation = isLandscape ? '横版' : '竖版';
            let displayName = paper.name;
            if (displayName.endsWith('_')) displayName = displayName.slice(0, -1);
            return {
                type: 'standard',
                label: `${displayName} · ${orientation}`,
                detail: `${w} × ${h} mm`,
                scale: 1,
                confidenceFrame: 1.0,
                confidenceScale: 1.0,
                isFallback: false,
            };
        }
    }
    return null;
}

// 等比缩放匹配：宽高比与标准幅面（含加长）相同（容差 ≤ 1%），整体缩放视为标准
function matchScaledStandard(w, h) {
    const ratio = w / h;
    const TOLERANCE = 0.01; // 1% 容差

    // 同时遍历 STANDARD_PAPERS（含加长幅面）和 BASE_PAPERS（基础幅面），
    // 确保 A1+1/4×100 等加长幅面的缩放尺寸也能被识别
    const allPapers = [...STANDARD_PAPERS, ...BASE_PAPERS];

    // 收集所有比例容差内的候选，选最优：
    // 1. 优先「缩放倍数最接近整数」（如 84100×59400 应选 A1×100 而非 A0×70.68）
    // 2. 整数倍相同时，优先「缩放倍数更小且 ≥ 1」（基础幅面尽可能大，
    //    如 A1+1×100 优于 A3+1×200；A2×50 优于 A4×100）
    // 3. 以上都相同，优先「宽高比偏差最小」
    // 门槛：缩放倍数必须接近整数（scaleDist ≤ 0.1），否则可能是比例巧合而非真实缩放
    const SCALE_DIST_MAX = 0.1;
    let best = null;
    for (const paper of allPapers) {
        const pRatio = paper.w / paper.h;
        const delta = Math.abs(ratio - pRatio) / pRatio;
        if (delta >= TOLERANCE) continue;

        const scaleW = w / paper.w;
        const scaleH = h / paper.h;
        const scale = (scaleW + scaleH) / 2;
        const scaleDist = Math.abs(scale - Math.round(scale));
        if (scaleDist > SCALE_DIST_MAX) continue;  // 缩放倍数不接近任何整数，跳过
        const scaleRounded = Math.round(scale);
        const isInteger = Math.abs(scale - scaleRounded) < 0.01;
        const effectiveScale = isInteger ? scaleRounded : scale;

        if (best === null || scaleDist < best.scaleDist - 1e-9 ||
            (Math.abs(scaleDist - best.scaleDist) < 1e-9 && effectiveScale >= 1 && (best.effectiveScale < 1 || effectiveScale < best.effectiveScale - 1e-9)) ||
            (Math.abs(scaleDist - best.scaleDist) < 1e-9 && Math.abs(effectiveScale - best.effectiveScale) < 1e-9 && delta < best.delta - 1e-9)) {
            best = { paper, delta, scale, scaleDist, effectiveScale };
        }
    }
    if (!best) return null;

    const paper = best.paper;
    const scale = best.scale;
    const scaleRounded = Math.round(scale);
    const isInteger = Math.abs(scale - scaleRounded) < 0.01;

    let displayName = paper.name;
    if (displayName.endsWith('_')) displayName = displayName.slice(0, -1);

    let label;
    if (isInteger && scaleRounded === 1) {
        // 与标准幅面几乎一致（尺寸误差在 1% 容差内，但非精确值）
        label = `${displayName} · 标准（尺寸误差 ≤1%）`;
    } else {
        const displayScale = isInteger ? scaleRounded : scale.toFixed(2);
        label = isInteger
            ? `${displayName} × ${displayScale} (缩放)`
            : `${displayName} (缩放 ~${displayScale}x)`;
    }
    return {
        type: 'standard',
        label: label,
        detail: `${w} × ${h} mm · 等比缩放`,
        scale: scale > 1 ? scale : 1,
        confidenceFrame: 1.0,
        confidenceScale: isInteger ? 1.0 : 0.9,
        isFallback: false,
    };
}

// 非标加长匹配：短边匹配基础幅面短边（10mm 容差），长边任意非标准长度，长/短 ≥ 2
function matchNonStandardElongated(w, h) {
    const short = Math.min(w, h);
    const long = Math.max(w, h);
    const TOLERANCE = 10.0; // 10mm 容差

    // 遍历基础幅面
    for (const paper of BASE_PAPERS) {
        const stdShort = Math.min(paper.w, paper.h);
        const stdLong = Math.max(paper.w, paper.h);
        if (Math.abs(short - stdShort) > TOLERANCE) continue;

        // 长边与短边比例至少为 2（避免把 A4 横竖版误判）
        if (long / short < 2) continue;

        // 长边是否为标准加长已由精确匹配（matchStandardExact）捕获，
        // 走到这里说明不是标准加长列表中的整数倍，认定为非标加长

        let displayName = paper.name;
        if (displayName.endsWith('_')) displayName = displayName.slice(0, -1);

        const isLandscape = (w >= h);
        const orientation = isLandscape ? '横版' : '竖版';
        const displayW = Math.round(long);
        const displayH = Math.round(short);
        return {
            type: 'extended',
            label: `非标加长 (${displayName} 短边)`,
            detail: `${displayW} × ${displayH} mm · ${orientation} · 短边 ${stdShort}mm`,
            scale: null,
            actualW: displayW,
            actualH: displayH,
            confidenceFrame: 0.7,
            confidenceScale: 0.6,
            isFallback: false,
        };
    }
    return null;
}

// 近似匹配（兜底）：宽高比最接近基础幅面者胜出，但设有两道门槛：
// - 比例偏差 > 40%：与任何幅面都不沾边，判「非标准图纸」（让第⑤级真正可达）
// - 任一边 < 100mm：比最小幅面 A4 短边(210)还小一半以上，不可能是图纸
const FALLBACK_MAX_RATIO_DELTA = 0.4;
const FALLBACK_MIN_SIDE = 100;

function matchNonStandardFallback(w, h) {
    const ratio = w / h;
    let best = null;
    let bestDelta = Infinity;
    for (const paper of BASE_PAPERS) {
        const pRatio = paper.w / paper.h;
        const delta = Math.abs(ratio - pRatio) / pRatio;
        if (delta < bestDelta) { bestDelta = delta; best = paper; }
    }
    if (!best) return null;
    if (bestDelta > FALLBACK_MAX_RATIO_DELTA) return null;
    if (Math.min(w, h) < FALLBACK_MIN_SIDE) return null;

    let displayName = best.name;
    if (displayName.endsWith('_')) displayName = displayName.slice(0, -1);
    // 置信度随偏差增大而单调递减：delta=0 时 0.7，偏差越大越接近 0.05
    let scaleConf = 0.7 * Math.exp(-6 * bestDelta);
    scaleConf = Math.min(Math.max(scaleConf, 0.05), 0.7);
    const isLandscape = (w >= h);
    const orientation = isLandscape ? '横版' : '竖版';
    const displayW = Math.round(Math.max(w, h));
    const displayH = Math.round(Math.min(w, h));
    return {
        type: 'fallback',
        label: `近似匹配：${displayName}（非标准）`,
        detail: `${displayW} × ${displayH} mm · ${orientation} · 宽高比接近 ${displayName}（偏差 ${(bestDelta * 100).toFixed(1)}%）`,
        scale: 1,
        actualW: displayW,
        actualH: displayH,
        confidenceFrame: 0.6,
        confidenceScale: scaleConf,
        isFallback: true,
        matchedPaper: displayName,
    };
}

function judgeSize(w, h) {
    w = Math.round(w);
    h = Math.round(h);
    if (w <= 0 || h <= 0) {
        return { type: 'nonstandard', label: '无效尺寸', detail: '尺寸必须为正数', confidenceFrame: 0, confidenceScale: 0 };
    }
    // 1. 精确匹配（含标准加长）
    const exact = matchStandardExact(w, h);
    if (exact) return exact;
    // 2. 等比缩放（标准）
    const scaled = matchScaledStandard(w, h);
    if (scaled) return scaled;
    // 3. 非标加长（短边匹配）
    const elongated = matchNonStandardElongated(w, h);
    if (elongated) return elongated;
    // 4. 近似匹配（有门槛的兜底：比例偏差>40% 或任一边<100mm 会落到第 5 级）
    const fallback = matchNonStandardFallback(w, h);
    if (fallback) return fallback;
    // 5. 彻底非标准
    return {
        type: 'nonstandard',
        label: '非标准图纸',
        detail: `${w} × ${h} mm · 不符合任何已知标准或加长规则`,
        confidenceFrame: 0,
        confidenceScale: 0,
        isFallback: false,
    };
}

function renderResult(result, container) {
    const map = {
        standard: { cls: 'standard', icon: '✅' },
        extended: { cls: 'extended', icon: '📏' },
        fallback: { cls: 'fallback', icon: '🔍' },
        nonstandard: { cls: 'nonstandard', icon: '❌' },
    };
    const info = map[result.type] || map.nonstandard;
    let scaleHtml = '';
    if (result.scale && result.scale > 1) {
        scaleHtml = `<span class="scale-tag">×${result.scale}</span>`;
    }
    let actualSizeHtml = '';
    if (result.actualW && result.actualH) {
        actualSizeHtml = `<div style="font-size:16px; font-weight:500; color:#0b1c33; margin-top:2px;">📐 实际尺寸：${result.actualW} × ${result.actualH} mm</div>`;
    }
    let confidenceHtml = '';
    if (result.confidenceFrame !== undefined && result.confidenceFrame > 0) {
        const framePct = (result.confidenceFrame * 100).toFixed(1);
        const scalePct = (result.confidenceScale * 100).toFixed(1);
        confidenceHtml = `
            <div class="confidence">
                <span class="item"><span class="tag">📊 图框置信度：</span><span class="value">${framePct}%</span></span>
                <span class="item"><span class="tag">📐 比例置信度：</span><span class="value">${scalePct}%</span></span>
            </div>
        `;
    }
    container.innerHTML = `
        <div class="label">📋 判断结果</div>
        <div class="value ${info.cls}">
            ${info.icon} ${result.label}
            ${scaleHtml}
            <span class="dim">· ${result.detail}</span>
            ${actualSizeHtml}
            ${confidenceHtml}
        </div>
    `;
}

function renderEmpty(container, msg) {
    container.innerHTML = `<div class="empty">${msg}</div>`;
}

// ================================================================
// 3. 手动输入
// ================================================================
const manualWidth = document.getElementById('manualWidth');
const manualHeight = document.getElementById('manualHeight');
const manualName = document.getElementById('manualName');
const manualResult = document.getElementById('manualResult');
const btnCheck = document.getElementById('btnCheckManual');
const btnClear = document.getElementById('btnClearManual');

function checkManual() {
    const w = parseFloat(manualWidth.value);
    const h = parseFloat(manualHeight.value);
    const name = manualName.value.trim() || '';
    if (isNaN(w) || isNaN(h) || w <= 0 || h <= 0) {
        renderEmpty(manualResult, '⚠️ 请输入有效的正数 (宽 × 高)');
        addFailedRecord(name || '手动输入', '无效尺寸（非正数）');
        return;
    }
    const wi = Math.round(w), hi = Math.round(h);
    if (Math.abs(w - wi) > 0.01 || Math.abs(h - hi) > 0.01) {
        renderEmpty(manualResult, '⚠️ 请输入整数尺寸 (mm)');
        addFailedRecord(name || '手动输入', '尺寸含有小数');
        return;
    }
    const result = judgeSize(wi, hi);
    renderResult(result, manualResult);
    addRecord(wi, hi, result, name);
}

btnCheck.addEventListener('click', checkManual);
btnClear.addEventListener('click', () => {
    manualWidth.value = '';
    manualHeight.value = '';
    manualName.value = '';
    renderEmpty(manualResult, '请输入宽高后点击「判断」');
});
manualWidth.addEventListener('keydown', (e) => { if (e.key === 'Enter') checkManual(); });
manualHeight.addEventListener('keydown', (e) => { if (e.key === 'Enter') checkManual(); });
manualName.addEventListener('keydown', (e) => { if (e.key === 'Enter') checkManual(); });

document.querySelectorAll('[data-w][data-h]').forEach(btn => {
    btn.addEventListener('click', () => {
        manualWidth.value = parseInt(btn.dataset.w);
        manualHeight.value = parseInt(btn.dataset.h);
        if (btn.dataset.name) manualName.value = btn.dataset.name;
        checkManual();
    });
});

// ================================================================
// 4. 批量上传（支持文件夹递归）
// ================================================================
const fileInput = document.getElementById('fileInput');
const uploadZone = document.getElementById('uploadZone');
const fileInfoContainer = document.getElementById('fileInfoContainer');
const fileName = document.getElementById('fileName');
const fileSize = document.getElementById('fileSize');
const fileCount = document.getElementById('fileCount');
const btnRemoveFile = document.getElementById('btnRemoveFile');
const dwgResult = document.getElementById('dwgResult');
const dwgStatus = document.getElementById('dwgStatus');
const dwgParseStatus = document.getElementById('dwgParseStatus');
const prioritySelect = document.getElementById('prioritySelect');
const unitSelect = document.getElementById('unitSelect');
const modeSelect = document.getElementById('modeSelect');
const batchStatus = document.getElementById('batchStatus');
const batchProgress = document.getElementById('batchProgress');
const batchCurrentFile = document.getElementById('batchCurrentFile');
const btnStopBatch = document.getElementById('btnStopBatch');

let isProcessing = false;
let stopRequested = false;

// 递归遍历文件夹（增强日志与错误处理）
async function traverseDirectory(entry, path) {
    return new Promise((resolve) => {
        if (entry.isFile) {
            const ext = entry.name.split('.').pop().toLowerCase();
            console.log(`[扫描] 文件: ${path ? path + '/' : ''}${entry.name} (扩展名: ${ext})`);
            if (ext === 'dwg' || ext === 'dxf') {
                entry.file((file) => {
                    file.relativePath = path ? `${path}/${entry.name}` : entry.name;
                    resolve(file);
                }, (err) => {
                    console.warn(`读取文件失败: ${entry.name}`, err);
                    resolve(null);
                });
            } else {
                resolve(null);
            }
        } else if (entry.isDirectory) {
            console.log(`[扫描] 进入目录: ${path ? path + '/' : ''}${entry.name}`);
            const reader = entry.createReader();
            const entries = [];
            const readEntries = () => {
                reader.readEntries((results) => {
                    if (results.length === 0) {
                        // 所有子条目读取完毕，递归处理
                        const subPath = path ? `${path}/${entry.name}` : entry.name;
                        Promise.all(entries.map(e => traverseDirectory(e, subPath)))
                            .then(results => {
                                const files = results.flat().filter(f => f !== null);
                                resolve(files);
                            })
                            .catch(err => {
                                console.warn(`读取子目录失败: ${subPath}`, err);
                                resolve([]);
                            });
                    } else {
                        entries.push(...results);
                        readEntries();
                    }
                }, (err) => {
                    console.warn(`读取目录条目失败: ${entry.name}`, err);
                    resolve([]);
                });
            };
            readEntries();
        } else {
            resolve(null);
        }
    });
}

// 从拖拽数据中获取文件列表（增强调试）
function getFilesFromDataTransfer(dataTransfer) {
    return new Promise((resolve) => {
        const items = dataTransfer.items;
        if (!items) {
            // 降级处理：直接使用 files（不支持文件夹）
            const files = Array.from(dataTransfer.files).filter(f => {
                const ext = f.name.split('.').pop().toLowerCase();
                return ext === 'dwg' || ext === 'dxf';
            });
            console.log(`[拖拽] 降级模式，获取到 ${files.length} 个文件`);
            resolve(files);
            return;
        }

        const entries = [];
        for (let i = 0; i < items.length; i++) {
            const item = items[i];
            if (item.kind === 'file') {
                const entry = item.webkitGetAsEntry();
                if (entry) {
                    entries.push(entry);
                } else {
                    console.warn(`[拖拽] 无法获取条目: ${item.type}`);
                }
            }
        }

        if (entries.length === 0) {
            console.warn('[拖拽] 未获取到任何条目');
            resolve([]);
            return;
        }

        console.log(`[拖拽] 开始扫描 ${entries.length} 个根条目...`);
        Promise.all(entries.map(entry => traverseDirectory(entry, '')))
            .then(results => {
                const allFiles = results.flat().filter(f => f !== null);
                console.log(`[拖拽] 扫描完成，共找到 ${allFiles.length} 个 DWG/DXF 文件`);
                resolve(allFiles);
            })
            .catch(err => {
                console.warn('[拖拽] 扫描失败:', err);
                resolve([]);
            });
    });
}

async function parseFiles(files) {
    if (isProcessing) return;
    if (!files || files.length === 0) return;

    // 记录本批次全部文件的相对路径（供生成删除脚本时计算待删除清单）
    for (const f of files) {
        const rel = (f.relativePath || f.name || '').split('/').join('\\');
        if (rel) scannedFiles.add(rel);
    }

    isProcessing = true;
    stopRequested = false;
    btnStopBatch.disabled = false;
    btnStopBatch.textContent = '⏹ 停止';
    batchStatus.style.display = 'flex';

    const total = files.length;
    let successCount = 0;
    let failCount = 0;
    let processedCount = 0;

    for (let i = 0; i < total; i++) {
        if (stopRequested) {
            batchProgress.textContent = `⏹ 已停止 | 已处理 ${i}/${total}，成功 ${successCount} 个，失败 ${failCount} 个`;
            dwgParseStatus.textContent = '⏹ 批量处理已停止';
            dwgParseStatus.style.color = '#b47d1f';
            break;
        }
        const file = files[i];
        const displayName = file.relativePath || file.name;
        batchProgress.textContent = `正在处理 ${processedCount + 1}/${total}：`;
        batchCurrentFile.textContent = displayName;
        try {
            const ok = await parseSingleFile(file);
            if (ok) {
                successCount++;
            } else {
                failCount++;
            }
        } catch (err) {
            failCount++;
        }
        processedCount++;
        batchProgress.textContent = `已处理 ${processedCount}/${total}，成功 ${successCount} 个，失败 ${failCount} 个`;
    }

    if (!stopRequested) {
        batchProgress.textContent = `✅ 批量处理完成！成功 ${successCount} 个，失败 ${failCount} 个`;
        batchCurrentFile.textContent = '';
        dwgParseStatus.textContent = `批量处理完成，成功 ${successCount} 个，失败 ${failCount} 个`;
        dwgParseStatus.style.color = '#0f7b4a';
        renderEmpty(dwgResult, `批量解析完成：成功 ${successCount} 个，失败 ${failCount} 个`);
    }

    btnStopBatch.disabled = true;
    btnStopBatch.textContent = '⏹ 已停止';
    isProcessing = false;
    stopRequested = false;
}

async function parseSingleFile(file) {
    let currentMode = modeSelect.value;
    let retried = false;
    // 完整相对路径（\ 分隔，带扩展名），用于删除脚本的保留保护
    const filePath = (file.relativePath || file.name || '').split('/').join('\\');

    while (true) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('priority', prioritySelect.value);
        formData.append('unit', unitSelect.value);
        formData.append('mode', currentMode);

        try {
            const response = await fetch('http://localhost:5000/upload', {
                method: 'POST',
                body: formData,
            });
            // 统一提取错误信息（后端出错时返回 HTTP 500 + JSON，成功时返回 200）
            let data = null;
            let errMsg = null;
            if (!response.ok) {
                try {
                    const errorData = await response.json();
                    errMsg = errorData.error || `HTTP ${response.status}`;
                } catch (_) {
                    errMsg = `HTTP ${response.status}`;
                }
            } else {
                data = await response.json();
                if (data.error) errMsg = data.error;
            }

            if (errMsg) {
                // 智能检测未找到图框时，自动切换为强制最大矩形模式重试一次
                if (errMsg.includes('未检测到图框') && currentMode === 'smart' && !retried) {
                    retried = true;
                    currentMode = 'force_max';
                    console.log(`[重试] ${file.relativePath || file.name} 智能检测未找到图框，自动切换为强制最大矩形模式重试`);
                    batchCurrentFile.textContent = (file.relativePath || file.name) + ' 🔄 重试...';
                    continue;
                }
                // 其他错误或重试后仍失败
                addFailedRecord(file.relativePath || file.name, errMsg, filePath);
                return false;
            }
            // 成功解析
            const w = data.width;
            const h = data.height;
            const result = judgeSize(w, h);
            const name = file.relativePath || file.name.replace(/\.[^.]+$/, '');
            // 多图框信息：后端返回本次检测到的全部图框候选
            const candidates = Array.isArray(data.candidates) ? data.candidates : [];
            const frameCount = (typeof data.frame_count === 'number' && data.frame_count > 0)
                ? data.frame_count
                : (candidates.length || 1);
            addRecord(w, h, result, name, filePath, { frameCount, candidates });
            return true;
        } catch (err) {
            addFailedRecord(file.relativePath || file.name, err.message || '未知错误', filePath);
            return false;
        }
    }
}

function stopBatch() {
    stopRequested = true;
    btnStopBatch.disabled = true;
    btnStopBatch.textContent = '⏹ 停止中...';
    batchProgress.textContent = '⏹ 正在停止（当前文件完成后停止）...';
    dwgParseStatus.textContent = '⏹ 用户请求停止，当前文件处理完成后将停止';
    dwgParseStatus.style.color = '#b47d1f';
}

btnStopBatch.addEventListener('click', stopBatch);

function handleFiles(fileList) {
    if (!fileList || fileList.length === 0) return;
    if (isProcessing) {
        if (confirm('当前正在处理批量任务，确定要重新开始吗？')) {
            stopRequested = true;
            setTimeout(() => {
                stopRequested = false;
                isProcessing = false;
                handleFiles(fileList);
            }, 500);
        }
        return;
    }

    const totalSize = fileList.reduce((sum, f) => sum + f.size, 0);
    fileCount.textContent = fileList.length;
    fileSize.textContent = (totalSize / 1024 / 1024).toFixed(1) + ' MB';
    fileInfoContainer.style.display = 'block';
    dwgStatus.textContent = '📄 已加载';
    dwgStatus.className = 'badge ready';

    renderEmpty(dwgResult, '⏳ 开始批量解析...');
    dwgParseStatus.textContent = '正在批量处理，请稍候...';
    dwgParseStatus.style.color = '#1a4b8c';

    parseFiles(fileList);
}

fileInput.addEventListener('change', (e) => {
    if (fileInput.files.length > 0) {
        const files = Array.from(fileInput.files);
        files.forEach(f => f.relativePath = f.name);
        handleFiles(files);
    }
});

uploadZone.addEventListener('click', () => fileInput.click());

uploadZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadZone.classList.add('dragover');
});
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));

// 修改 drop 事件，增加详细信息提示
uploadZone.addEventListener('drop', async (e) => {
    e.preventDefault();
    uploadZone.classList.remove('dragover');
    try {
        const files = await getFilesFromDataTransfer(e.dataTransfer);
        if (files.length === 0) {
            // 获取统计信息（通过控制台输出）
            console.warn('[拖拽] 未找到 DWG/DXF 文件，请检查控制台日志了解扫描详情。');
            alert('未找到有效的 DWG/DXF 文件。\n\n可能原因：\n1. 文件夹中确实没有 .dwg 或 .dxf 文件。\n2. 浏览器权限限制无法读取子文件夹。\n3. 文件名包含特殊字符导致读取失败。\n\n请尝试使用“点击上传”方式选择单个或多个文件（不支持文件夹）。');
            return;
        }
        handleFiles(files);
    } catch (err) {
        console.error('拖拽处理错误:', err);
        alert('拖拽处理失败，请尝试点击上传。错误信息: ' + err.message);
    }
});


btnRemoveFile.addEventListener('click', () => {
    if (isProcessing) {
        if (!confirm('批量处理正在进行，确定要清空吗？')) return;
        stopRequested = true;
    }
    fileInput.value = '';
    fileInfoContainer.style.display = 'none';
    dwgStatus.textContent = '需本地服务';
    dwgStatus.className = 'badge';
    renderEmpty(dwgResult, '上传文件后自动批量解析');
    dwgParseStatus.textContent = '';
    batchStatus.style.display = 'none';
    isProcessing = false;
    stopRequested = false;
    btnStopBatch.disabled = true;
    btnStopBatch.textContent = '⏹ 停止';
});

// ================================================================
// 5. 测试记录管理（含筛选、导入导出、生成删除脚本）
// ================================================================
const STORAGE_KEY = 'dwg_test_records';
let records = [];
let recordIdCounter = 0;
// 会话级"原始文件全集"：记录每次拖入/选择文件夹时扫描到的全部 DWG/DXF 相对路径（\ 分隔）。
// 生成删除脚本时，用它减去"保留记录"得到精确的待删除清单。
const scannedFiles = new Set();

function loadRecords() {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored) {
            const parsed = JSON.parse(stored);
            if (Array.isArray(parsed)) {
                records = parsed;
                if (records.length > 0) {
                    const maxId = Math.max(...records.map(r => r.id || 0));
                    recordIdCounter = maxId;
                }
                return;
            }
        }
    } catch (e) { console.warn('读取存储失败', e); }
    records = [];
    recordIdCounter = 0;
}

function saveRecords() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(records));
    } catch (e) { console.warn('保存存储失败', e); }
}

function getRecordType(rec) {
    if (rec.isFailed) return 'failed';
    return rec.type || 'nonstandard';
}

// ---------- 混合图纸（标准 + 非标并存） ----------
// 一个 dwg 里可能同时检出"标准图框"与"非标图框"（如 16 张标准图 + 1 张非标封面）。
// 文件级类型由主框决定（不破坏现有 4 类），此处额外识别"混合"并逐框统计构成。
// 不改变 getRecordType() 返回值，仅作为附加徽标与筛选维度。

// 逐框分类统计：framesMeta（结构化候选）→ { type -> count }，旧记录无 framesMeta 返回 null
function classifyFrames(rec) {
    const meta = rec && Array.isArray(rec.framesMeta) ? rec.framesMeta : null;
    if (!meta || meta.length === 0) return null;
    const groups = {};
    let valid = 0;
    meta.forEach(m => {
        if (!m.w || !m.h || m.w <= 0 || m.h <= 0) return;
        const t = judgeSize(m.w, m.h).type;
        groups[t] = (groups[t] || 0) + 1;
        valid += 1;
    });
    if (valid === 0) return null;
    return groups;
}

// 是否"混合"：同一文件内"标准族（standard）"与"非标族（extended/fallback/nonstandard）"
// 图框并存（如 16 张标准图 + 1 张非标封面）。前端收到的候选已过特征筛选，都是有效
// 图框，因此只要两族各 ≥1 即视为混合——不存在"伪框噪声"把文件误标混合的问题。
// 全非标（如 3 张非标加长）或全标准 → 不标混合，维持单族语义。
function isMixedRecord(rec) {
    const groups = classifyFrames(rec);
    if (!groups) return false;
    const std = groups.standard || 0;
    const total = Object.values(groups).reduce((s, n) => s + n, 0);
    return std > 0 && std < total;
}

// 混合构成的展示摘要：如 "标准×16 · 非标准×1"
function mixedSummary(rec) {
    const groups = classifyFrames(rec);
    if (!groups) return '';
    const short = {
        standard: '标准',
        extended: '非标加长',
        fallback: '近似',
        nonstandard: '非标准',
        failed: '失败',
    };
    // 按数量降序
    return Object.entries(groups)
        .sort((a, b) => b[1] - a[1])
        .map(([t, n]) => `${short[t] || t}×${n}`)
        .join(' · ');
}

function addFailedRecord(name, errorMsg, path) {
    const timestamp = new Date().toLocaleString('zh-CN', { hour12: false });
    const record = {
        id: ++recordIdCounter,
        name: name || '未知文件',
        path: path || '',
        w: null,
        h: null,
        type: 'failed',
        actualResult: `❌ 解析失败：${errorMsg || '未知错误'}`,
        frameConf: '',
        scaleConf: '',
        expected: '',
        match: 'pending',
        note: `失败时间：${timestamp}`,
        isFailed: true,
    };
    records.push(record);
    saveRecords();
    renderRecords();
}

// 把多图框候选格式化为一行摘要：841×594(模型空间)、594×420(布局 "A") …
function formatFramesText(candidates) {
    return candidates.slice(0, 10).map(c => `${c.width}×${c.height}(${c.layout})`).join('、')
        + (candidates.length > 10 ? ` 等 ${candidates.length} 个` : '');
}

// 全量候选文本（供气泡完整展示 / 一键复制），不截断
function formatFramesAll(candidates) {
    return candidates.map(c => `${c.width}×${c.height}(${c.layout})`).join('、');
}

function addRecord(w, h, result, name, path, framesInfo) {
    const framePct = result.confidenceFrame !== undefined ? (result.confidenceFrame * 100).toFixed(1) : '';
    const scalePct = result.confidenceScale !== undefined ? (result.confidenceScale * 100).toFixed(1) : '';
    const frameCount = (framesInfo && framesInfo.frameCount) || 1;
    const candidates = (framesInfo && framesInfo.candidates) || [];
    // 多图框信息由记录表「图框数」列展示，这里不再拼进实际判断结果
    const actualResult = result.label + ' · ' + result.detail;
    // 结构化候选（每框 w/h/layout），供逐框分类判断"标准+非标混合"
    const framesMeta = candidates.map(c => ({
        w: c.width,
        h: c.height,
        layout: c.layout,
    }));
    const record = {
        id: ++recordIdCounter,
        name: name || '',
        path: path || '',
        w: w,
        h: h,
        type: result.type || 'nonstandard',
        actualResult: actualResult,
        frameConf: framePct,
        scaleConf: scalePct,
        expected: '',
        match: 'pending',
        note: '',
        isFailed: false,
        frameCount: frameCount,
        framesText: frameCount > 1 ? formatFramesText(candidates) : '',
        framesAll: frameCount > 1 ? formatFramesAll(candidates) : '',
        framesMeta: framesMeta,
    };
    records.push(record);
    saveRecords();
    renderRecords();
}

function deleteRecord(id) {
    records = records.filter(r => r.id !== id);
    selectedIds.delete(id);
    saveRecords();
    renderRecords();
}

// 批量勾选：被勾选记录的 id 集合（行勾选 / 表头全选共用）
let selectedIds = new Set();

// 更新表头全选框与批量删除按钮状态
function updateBatchUI() {
    const checkAll = document.getElementById('checkAll');
    const countSpan = document.getElementById('batchDeleteCount');
    const batchBtn = document.getElementById('batchDeleteBtn');
    const filtered = getFilteredRecords();
    const visibleIds = filtered.map(r => r.id);
    const allChecked = visibleIds.length > 0 && visibleIds.every(id => selectedIds.has(id));
    const anyChecked = visibleIds.some(id => selectedIds.has(id));
    if (checkAll) {
        checkAll.checked = allChecked;
        checkAll.indeterminate = anyChecked && !allChecked;
    }
    const n = records.filter(r => selectedIds.has(r.id)).length;
    if (countSpan) countSpan.textContent = n;
    if (batchBtn) batchBtn.disabled = n === 0;
}

function updateRecordField(id, field, value) {
    const rec = records.find(r => r.id === id);
    if (rec) {
        rec[field] = value;
        saveRecords();
        renderRecords();
    }
}

// ---------- 筛选功能（多选复选框） ----------
// 获取当前选中的筛选类型列表
function getActiveFilters() {
    const checkboxes = document.querySelectorAll('#filterCheckboxes input[type="checkbox"]');
    const active = [];
    checkboxes.forEach(cb => {
        if (cb.checked) {
            active.push(cb.value);
        }
    });
    return active;
}

// 判断记录是否匹配当前筛选
function recordMatchesFilter(rec) {
    const activeFilters = getActiveFilters();
    if (activeFilters.length === 0) return true; // 无选中则显示全部
    // "混合"是附加维度：勾选时混合记录直接命中（其主导类型可能仍是 standard 等）。
    // 未勾选"混合"时，混合记录仍按主导类型参与原 4 类过滤，不破坏现有分类视图。
    if (activeFilters.includes('mixed') && isMixedRecord(rec)) return true;
    const recType = getRecordType(rec);
    return activeFilters.includes(recType);
}

function getFilteredRecords() {
    return records.filter(r => recordMatchesFilter(r));
}

// 筛选复选框变化时重新渲染
document.querySelectorAll('#filterCheckboxes input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', renderRecords);
});

function renderRecords() {
    const tbody = document.getElementById('recordBody');
    const countSpan = document.getElementById('recordCount');
    const filterCountSpan = document.getElementById('filterCount');

    const filtered = getFilteredRecords();
    countSpan.textContent = records.length + ' 条';
    filterCountSpan.textContent = `当前筛选：${filtered.length} 条`;

    if (records.length === 0) {
        tbody.innerHTML = `<tr><td colspan="14" class="record-empty">暂无记录</td></tr>`;
        updateBatchUI();
        return;
    }

    if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="14" class="record-empty">无匹配记录</td></tr>`;
        updateBatchUI();
        return;
    }

    const typeLabels = {
        standard: { label: '标准', cls: 'standard' },
        extended: { label: '非标加长', cls: 'extended' },
        fallback: { label: '近似匹配', cls: 'fallback' },
        nonstandard: { label: '非标准', cls: 'nonstandard' },
        failed: { label: '解析失败', cls: 'failed' },
    };

    let html = '';
    filtered.forEach((rec, index) => {
        const matchOptions = `
            <select class="editable-select" data-id="${rec.id}" data-field="match">
                <option value="pending" ${rec.match === 'pending' ? 'selected' : ''}>➖ 待确认</option>
                <option value="yes" ${rec.match === 'yes' ? 'selected' : ''}>✅ 是</option>
                <option value="no" ${rec.match === 'no' ? 'selected' : ''}>❌ 否</option>
            </select>
        `;
        const matchClass = rec.match === 'yes' ? 'match-yes' : (rec.match === 'no' ? 'match-no' : 'match-pending');
        const wDisplay = (rec.w && rec.w > 0) ? rec.w : '—';
        const hDisplay = (rec.h && rec.h > 0) ? rec.h : '—';
        const frameDisplay = rec.frameConf || '—';
        const scaleDisplay = rec.scaleConf || '—';
        const rowClass = rec.isFailed ? 'record-failed' : '';
        const typeInfo = typeLabels[getRecordType(rec)] || { label: '未知', cls: 'nonstandard' };
        // 混合徽章：文件内同时检出"标准 + 非标"等多类图框（如 16 标准 + 1 封面非标）。
        // 仅附加展示，不改变主导类型（不破坏现有 4 类筛选/统计）。
        const mixedHtml = isMixedRecord(rec)
            ? `<span class="type-tag mixed" title="构成：${escHtml(mixedSummary(rec))}">🌀 混合</span>`
            : '';
        const typeHtml = `<span class="type-tag ${typeInfo.cls}">${typeInfo.label}</span>${mixedHtml}`;
        // 图框数列：展示该图纸检测到的图框数量（模型空间 + 布局空间合计）。
        // 多图框时用紫色徽章，悬停可查看全部候选的尺寸与所在空间；手动输入/失败记录显示 —。
        const hasFrameCount = rec.frameCount && rec.frameCount > 0;
        let frameCountHtml = '—';
        if (hasFrameCount) {
            frameCountHtml = (rec.frameCount > 1)
                ? `<span class="type-tag multi-frame tip-anchor" data-frames="${escHtml(rec.framesText || '')}" data-all="${escHtml(rec.framesAll || rec.framesText || '')}">🖼 ×${rec.frameCount}</span>`
                : String(rec.frameCount);
        }

        html += `<tr class="${rowClass}">
            <td class="col-check"><input type="checkbox" class="row-check" data-id="${rec.id}" ${selectedIds.has(rec.id) ? 'checked' : ''} /></td>
            <td>${index + 1}</td>
            <td><input class="editable" type="text" data-id="${rec.id}" data-field="name" value="${escHtml(rec.name)}" placeholder="图纸名称" style="min-width:100px;" /></td>
            <td>${wDisplay}</td>
            <td>${hDisplay}</td>
            <td>${frameCountHtml}</td>
            <td>${typeHtml}</td>
            <td style="max-width:280px; white-space:normal; word-break:break-word;">${escHtml(rec.actualResult)}</td>
            <td>${frameDisplay}</td>
            <td>${scaleDisplay}</td>
            <td><input class="editable" type="text" data-id="${rec.id}" data-field="expected" value="${escHtml(rec.expected)}" placeholder="如 A0" style="min-width:70px;" /></td>
            <td class="${matchClass}">${matchOptions}</td>
            <td><input class="editable" type="text" data-id="${rec.id}" data-field="note" value="${escHtml(rec.note)}" placeholder="备注" style="min-width:80px;" /></td>
            <td><button class="btn-del" data-id="${rec.id}" title="删除此行">✕</button></td>
        </tr>`;
    });
    tbody.innerHTML = html;

    tbody.querySelectorAll('.row-check').forEach(cb => {
        cb.addEventListener('change', function() {
            const id = parseInt(this.dataset.id);
            if (this.checked) {
                selectedIds.add(id);
            } else {
                selectedIds.delete(id);
            }
            updateBatchUI();
        });
    });
    tbody.querySelectorAll('.editable').forEach(inp => {
        inp.addEventListener('change', function() {
            const id = parseInt(this.dataset.id);
            const field = this.dataset.field;
            updateRecordField(id, field, this.value);
        });
    });
    tbody.querySelectorAll('.editable-select').forEach(sel => {
        sel.addEventListener('change', function() {
            const id = parseInt(this.dataset.id);
            updateRecordField(id, 'match', this.value);
        });
    });
    tbody.querySelectorAll('.btn-del').forEach(btn => {
        btn.addEventListener('click', function() {
            const id = parseInt(this.dataset.id);
            if (confirm('确定删除该记录吗？')) {
                deleteRecord(id);
            }
            if (tipPop && tipPop.__pinned) hideTip();
        });
    });

    // 多图框徽章：可截图气泡（悬停弹出 / 移开保留片刻 / 点击钉住常显）
    bindTipAnchors(tbody);

    updateBatchUI();
}

// ---------- 图框数徽章气泡（可截图） ----------
// 浏览器原生 title 气泡随鼠标移动消失，无法截图。改用自绘 fixed 定位气泡：
//   - mouseenter 弹出完整列表（>10 个时顶部带"…等 N 个"摘要，列表可滚动看全部）
//   - mouseleave 后保留 TIP_KEEP_MS 再消失（留出截图时间窗）；悬停操作区不消失
//   - 气泡底部按钮：📌 固定（常显，配合截图）/ 再次点击关闭；📋 复制全部（全量数据）
const TIP_KEEP_MS = 2500;          // 鼠标移出后气泡保留时长（供截图 / 移向操作区）
let tipPop = null;                 // 气泡单例元素
let tipAnchorEl = null;            // 当前锚点
let tipHideTimer = null;
let tipPinned = false;

function ensureTipPop() {
    if (!tipPop) {
        tipPop = document.createElement('div');
        tipPop.className = 'tip-pop';
        tipPop.style.display = 'none';
        document.body.appendChild(tipPop);
    }
    return tipPop;
}

function showTip(anchor) {
    const framesText = anchor.dataset.frames || '';   // 摘要（前10 + 等 N 个，展示用）
    const allText = anchor.dataset.all || framesText; // 全量明细（复制用）
    const rawLines = String(allText).split('、').filter(Boolean);
    const nRaw = rawLines.length;
    if (nRaw === 0) return;
    ensureTipPop();
    // 按尺寸聚合：相同 宽×高 只显示一个，计数 ×n（保持原出现顺序）
    const dimOrder = [];
    const dimCount = new Map();
    rawLines.forEach(line => {
        const dim = line.split('(')[0].trim();   // 尺寸部分：841×594
        if (!dimCount.has(dim)) {
            dimCount.set(dim, 0);
            dimOrder.push(dim);
        }
        dimCount.set(dim, dimCount.get(dim) + 1);
    });
    const dimRows = dimOrder.map(dim => {
        const n = dimCount.get(dim);
        return `<div class="tip-line">${escHtml(dim)}${n > 1 ? ` <span class="tip-times">×${n}</span>` : ''}</div>`;
    }).join('');
    const nDim = dimOrder.length;
    const truncated = nRaw > 20;                      // 原始图框超 20 个才显示"…等 N 个"摘要行
    tipPop.innerHTML =
        `<div class="tip-head">共 ${nRaw} 个图框${nDim > 1 ? `（${nDim} 种尺寸）` : ''}${truncated ? ' <span class="tip-more">· 下方滚动查看</span>' : ''}</div>`
        + (truncated ? `<div class="tip-summary" title="${escHtml(framesText)}">${escHtml(framesText)}</div>` : '')
        + `<div class="tip-list">${dimRows}</div>`
        + `<div class="tip-actions">
             <button type="button" class="tip-btn tip-pin">${tipPinned ? '📌 已固定' : '📌 固定'}</button>
             <button type="button" class="tip-btn tip-copy">📋 复制全部</button>
           </div>`;
    positionTip(anchor);
    tipPop.style.display = 'block';
    clearTimeout(tipHideTimer);
    bindTipActions();
    // 鼠标移开时若非钉住，延迟保留后隐藏（留截图窗口）
    if (!tipPinned) {
        tipHideTimer = setTimeout(() => { hideTip(); }, TIP_KEEP_MS);
    }
}

function bindTipActions() {
    // 操作区可交互（气泡其余区域 pointer-events:none）；悬停其上暂停自动隐藏
    const actions = tipPop.querySelector('.tip-actions');
    if (!actions) return;
    actions.addEventListener('mouseenter', () => clearTimeout(tipHideTimer));
    actions.addEventListener('mouseleave', () => {
        if (!tipPinned && tipPop.style.display === 'block') {
            clearTimeout(tipHideTimer);
            tipHideTimer = setTimeout(() => { hideTip(); }, TIP_KEEP_MS);
        }
    });
    actions.querySelector('.tip-pin').addEventListener('click', ev => {
        ev.stopPropagation();
        if (tipPinned) { hideTip(); return; }   // 再次点击已固定的气泡 → 关闭
        tipPinned = true;
        clearTimeout(tipHideTimer);
        tipPop.querySelector('.tip-pin').textContent = '📌 已固定';
    });
    const copyBtn = actions.querySelector('.tip-copy');
    copyBtn.addEventListener('click', async ev => {
        ev.stopPropagation();
        const allText = tipAnchorEl ? (tipAnchorEl.dataset.all || tipAnchorEl.dataset.frames || '') : '';
        const payload = String(allText).split('、').filter(Boolean).join('\r\n');
        const done = () => {
            copyBtn.classList.add('copied');
            copyBtn.textContent = '✓ 已复制';
            setTimeout(() => { copyBtn.classList.remove('copied'); copyBtn.textContent = '📋 复制全部'; }, 1600);
        };
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(payload);
            } else {
                const ta = document.createElement('textarea');
                ta.value = payload;
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                ta.remove();
            }
            done();
        } catch (e) {
            copyBtn.textContent = '⚠ 复制失败';
            setTimeout(() => { copyBtn.textContent = '📋 复制全部'; }, 1600);
        }
    });
}

function positionTip(anchor) {
    const r = anchor.getBoundingClientRect();
    const pop = tipPop;
    pop.style.visibility = 'hidden';
    pop.style.display = 'block';
    const pw = pop.offsetWidth, ph = pop.offsetHeight;
    let x = r.left + r.width / 2 - pw / 2;
    let y = r.bottom + 8;
    // 视口越界回弹
    if (x + pw > window.innerWidth - 8) x = Math.max(8, window.innerWidth - pw - 8);
    if (x < 8) x = 8;
    if (y + ph > window.innerHeight - 8) {
        y = r.top - ph - 8;   // 放上方
        if (y < 8) y = 8;
    }
    pop.style.left = x + 'px';
    pop.style.top = y + 'px';
    pop.style.visibility = 'visible';
}

function hideTip() {
    if (tipPop) {
        tipPop.style.display = 'none';
        tipPinned = false;
        tipAnchorEl = null;
    }
    clearTimeout(tipHideTimer);
}

function bindTipAnchors(root) {
    root.querySelectorAll('.tip-anchor').forEach(el => {
        el.addEventListener('mouseenter', function() {
            tipPinned = false;
            tipAnchorEl = this;
            showTip(this);
        });
        el.addEventListener('click', function(ev) {
            ev.stopPropagation();
            if (tipPop && tipPop.style.display === 'block' && this === tipAnchorEl) {
                if (tipPinned) { hideTip(); return; }      // 再次点击钉住的徽章 → 关闭
                tipPinned = true;                          // 未钉住点击 → 钉住常显
            } else {
                tipPinned = true;
                tipAnchorEl = this;
            }
            showTip(this);
        });
    });
}

// 点击页面其它区域关闭钉住的气泡；滚动/窗口缩放时隐藏（位置可能失效）
document.addEventListener('click', function() {
    if (tipPinned) hideTip();
});
['scroll', 'resize'].forEach(evt => {
    window.addEventListener(evt, function() {
        if (tipPop && tipPop.style.display === 'block') hideTip();
    });
}, { capture: true });

function escHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ---------- 导出 CSV ----------
function exportCsv(filtered) {
    if (records.length === 0) { alert('暂无记录可导出'); return; }
    const data = filtered || records;
    if (data.length === 0) { alert('当前筛选结果为空，无可导出记录'); return; }

    const headers = ['序号', '图纸名称', '宽(mm)', '高(mm)', '类型', '实际判断结果', '图框置信度(%)', '比例置信度(%)', '预期幅面', '匹配预期', '备注', '图框数'];
    const rows = data.map((rec, idx) => [
        idx + 1,
        rec.name || '',
        (rec.w && rec.w > 0) ? rec.w : '',
        (rec.h && rec.h > 0) ? rec.h : '',
        getRecordType(rec),
        rec.actualResult,
        rec.frameConf || '',
        rec.scaleConf || '',
        rec.expected,
        rec.match === 'yes' ? '是' : (rec.match === 'no' ? '否' : '待确认'),
        rec.note,
        (rec.frameCount && rec.frameCount > 0) ? rec.frameCount : 1,
    ]);
    let csv = '\uFEFF' + headers.join(',') + '\n';
    rows.forEach(row => {
        const escaped = row.map(cell => {
            if (typeof cell === 'string' && (cell.includes(',') || cell.includes('"') || cell.includes('\n'))) {
                return '"' + cell.replace(/"/g, '""') + '"';
            }
            return cell;
        });
        csv += escaped.join(',') + '\n';
    });
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    // 根据当前勾选的筛选类型生成文件名后缀
    const typeLabelsShort = {
        standard: '标准',
        extended: '非标加长',
        fallback: '近似匹配',
        nonstandard: '非标准',
        failed: '解析失败',
        mixed: '混合',
    };
    const allTypes = ['standard', 'extended', 'fallback', 'nonstandard', 'failed', 'mixed'];
    const active = getActiveFilters();
    const suffix = (active.length === allTypes.length || active.length === 0)
        ? '全部'
        : active.map(t => typeLabelsShort[t] || t).join('+');
    link.download = `图框检测记录_${suffix}_${new Date().toISOString().slice(0,10)}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

document.getElementById('exportCsvBtn').addEventListener('click', function() {
    exportCsv(getFilteredRecords());
});

document.getElementById('exportAllCsvBtn').addEventListener('click', function() {
    exportCsv(records);
});

// ---------- 导入 CSV ----------
document.getElementById('importCsvBtn').addEventListener('click', function() {
    document.getElementById('importFileInput').click();
});

document.getElementById('importFileInput').addEventListener('change', function(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function(event) {
        const csvText = event.target.result;
        const lines = csvText.split('\n').filter(line => line.trim() !== '');
        if (lines.length < 2) {
            alert('CSV 文件为空或格式不正确');
            return;
        }
        const headers = lines[0].split(',').map(h => h.trim());
        const hasName = headers.some(h => h.includes('图纸名称') || h.includes('名称'));
        if (!hasName) {
            if (!confirm('CSV 表头似乎与导出的格式不符，是否继续导入？')) return;
        }
        let importedCount = 0;
        const newRecords = [];
        for (let i = 1; i < lines.length; i++) {
            const cols = lines[i].split(',').map(c => c.trim());
            if (cols.length < 11) continue;
            const name = cols[1] || '';
            const w = parseInt(cols[2]) || 0;
            const h = parseInt(cols[3]) || 0;
            const type = cols[4] || 'nonstandard';
            const actualResult = cols[5] || '';
            const frameConf = cols[6] || '';
            const scaleConf = cols[7] || '';
            const expected = cols[8] || '';
            const matchRaw = cols[9] || '待确认';
            let match = 'pending';
            if (matchRaw === '是') match = 'yes';
            else if (matchRaw === '否') match = 'no';
            const note = cols[10] || '';
            const frameCount = parseInt(cols[11]) || 1;
            const isFailed = (w <= 0 || h <= 0) || type === 'failed' || actualResult.includes('解析失败');
            const record = {
                id: ++recordIdCounter,
                name: name,
                w: (w > 0) ? w : null,
                h: (h > 0) ? h : null,
                type: isFailed ? 'failed' : (type || 'nonstandard'),
                actualResult: actualResult || '（空）',
                frameConf: frameConf,
                scaleConf: scaleConf,
                expected: expected,
                match: match,
                note: note,
                isFailed: isFailed,
                frameCount: frameCount,
                framesText: '',
            };
            newRecords.push(record);
            importedCount++;
        }
        if (importedCount === 0) {
            alert('未解析到有效记录，请检查 CSV 格式是否与导出的格式一致。');
        } else {
            records.push(...newRecords);
            saveRecords();
            renderRecords();
            alert(`成功导入 ${importedCount} 条记录！`);
        }
        document.getElementById('importFileInput').value = '';
    };
    reader.readAsText(file, 'UTF-8');
});

document.getElementById('clearRecordsBtn').addEventListener('click', function() {
    if (records.length === 0) return;
    if (confirm('确定清空所有测试记录吗？（本地存储中的数据也将被删除）')) {
        records = [];
        recordIdCounter = 0;
        selectedIds.clear();
        saveRecords();
        renderRecords();
    }
});

// 表头全选：作用于当前筛选可见的所有记录（再次点击若全部选中则取消全选）
document.getElementById('checkAll').addEventListener('change', function() {
    const filtered = getFilteredRecords();
    if (this.checked) {
        filtered.forEach(r => selectedIds.add(r.id));
    } else {
        filtered.forEach(r => selectedIds.delete(r.id));
    }
    renderRecords();
});

// 批量删除已勾选记录
document.getElementById('batchDeleteBtn').addEventListener('click', function() {
    const ids = records.filter(r => selectedIds.has(r.id)).map(r => r.id);
    if (ids.length === 0) return;
    if (confirm(`确定删除已勾选的 ${ids.length} 条记录吗？`)) {
        const del = new Set(ids);
        records = records.filter(r => !del.has(r.id));
        selectedIds.clear();
        saveRecords();
        renderRecords();
    }
});

document.getElementById('genScriptBtn').addEventListener('click', function() {
    const filtered = getFilteredRecords();
    if (filtered.length === 0) {
        alert('当前筛选结果为空，无法生成脚本。请先筛选出需要保留的图纸。');
        return;
    }

    // ---- 1. 构建"保留文件"候选集合（大小写不敏感，宁可多保留、不可误删） ----
    // 优先使用记录中的完整相对路径（path）；旧记录无 path 时用 name 尝试补扩展名
    const keepSet = new Set();
    const norm = s => s.replace(/\\/g, '\\').toLowerCase();
    filtered.forEach(rec => {
        const candidates = [];
        if (rec.path && rec.path.trim()) candidates.push(rec.path.trim());
        if (rec.name && rec.name.trim()) {
            const p = rec.name.trim().split('/').join('\\');
            candidates.push(p);
            if (!/\.(dwg|dxf)$/i.test(p)) {
                candidates.push(p + '.dwg');
                candidates.push(p + '.dxf');
            }
        }
        candidates.forEach(c => keepSet.add(norm(c)));
    });

    // ---- 2. 计算待删除清单 = 原始文件全集 - 保留集合 ----
    if (scannedFiles.size === 0) {
        alert('尚未建立文件清单：请先通过「拖拽文件夹」或「选择文件夹」的方式载入原始图纸目录并完成批量解析，\n之后才能精确计算哪些文件不在保留范围内。');
        return;
    }
    const deleteList = [...scannedFiles].filter(p => !keepSet.has(norm(p)));
    if (deleteList.length === 0) {
        alert('✅ 没有需要删除的文件：原始文件全集中的每一个文件都在当前筛选结果的保留范围内。');
        return;
    }

    // ---- 3. 删除前校验：展示完整清单，必须人工确认 ----
    const PREVIEW_LIMIT = 30;
    const previewLines = deleteList.slice(0, PREVIEW_LIMIT).map(p => '  ' + p);
    if (deleteList.length > PREVIEW_LIMIT) {
        previewLines.push(`  ... 等 ${deleteList.length - PREVIEW_LIMIT} 个`);
    }
    const topDirs = [...new Set(deleteList.map(p => p.split('\\')[0]))];
    const placeHint = topDirs.length === 1
        ? `请把两个文件放到「${topDirs[0]}」的上级目录（与「${topDirs[0]}」同级）后运行`
        : '请把两个文件放到包含以上所有路径的公共目录下运行';
    const confirmed = confirm(
        `⚠️ 生成删除脚本前请确认\n\n` +
        `当前筛选结果（保留）：${filtered.length} 条记录\n` +
        `将删除 ${deleteList.length} 个文件（不在保留范围内）：\n\n` +
        previewLines.join('\n') + `\n\n` +
        `运行脚本后这些文件将被永久删除（不进回收站）！\n是否继续生成？`
    );
    if (!confirmed) {
        alert('已取消，未生成任何文件。');
        return;
    }

    // ---- 4. 生成 delete_list.txt（CRLF，无 BOM UTF-8） ----
    const listContent = deleteList.join('\r\n') + '\r\n';
    const txtBlob = new Blob([listContent], { type: 'text/plain;charset=utf-8' });
    const txtLink = document.createElement('a');
    txtLink.href = URL.createObjectURL(txtBlob);
    txtLink.download = 'delete_list.txt';
    document.body.appendChild(txtLink);
    txtLink.click();
    document.body.removeChild(txtLink);

    // ---- 5. 生成 delete_unwanted.bat ----
    // 注意：cmd 批处理解析器不认 UTF-8 BOM，因此 bat 内容必须为纯 ASCII（提示用英文），
    // 中文文件名靠 chcp 65001 + UTF-8 清单文件（delete_list.txt）处理。
    // 脚本只做一件事：按 delete_list.txt 逐行确认删除并回显，不再做任何"扫描取反"，
    // 因此不受路径分隔符、findstr 中文编码、%cd% 特殊字符等问题影响。
    let bat = `@echo off
chcp 65001 >nul
title DWG Cleanup Script
echo ==========================================
echo   DWG Cleanup Script
echo   Current directory: %cd%
echo ==========================================
echo.
if not exist "delete_list.txt" (
    echo [ERROR] delete_list.txt not found.
    echo Put delete_list.txt next to this script and run again.
    pause
    exit /b
)
echo The following ${deleteList.length} file(s) will be deleted:
echo ------------------------------------------
type delete_list.txt
echo ------------------------------------------
choice /C YN /M "Delete all above files?"
if errorlevel 2 (
    echo.
    echo Cancelled. Nothing was deleted.
    pause
    exit /b
)
echo.
echo Deleting...
if exist delete_log.txt del delete_log.txt
set /a ok=0
set /a miss=0
set /a fail=0
for /f "usebackq delims=" %%i in ("delete_list.txt") do (
    if exist "%%i" (
        del "%%i" 2>nul
        if errorlevel 1 (
            echo [FAILED] "%%i"
            echo [FAILED] "%%i">>delete_log.txt
            set /a fail+=1
        ) else (
            echo [DELETED] "%%i"
            echo [DELETED] "%%i">>delete_log.txt
            set /a ok+=1
        )
    ) else (
        echo [NOT FOUND] "%%i"
        echo [NOT FOUND] "%%i">>delete_log.txt
        set /a miss+=1
    )
)
echo.
echo ==========================================
echo   Done: deleted %ok%, missing %miss%, failed %fail%
echo ==========================================
echo.
echo Cleaning up empty directories (files only)...
for /d /r %%d in (*) do rd "%%d" 2>nul
echo.
echo Finished! Details saved to delete_log.txt
pause
`;

    // bat 必须无 BOM（cmd 不识别 UTF-8 BOM）
    const blob = new Blob([bat], { type: 'text/plain;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'delete_unwanted.bat';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    alert(
        `已生成 2 个文件：\n` +
        `1. delete_list.txt（待删除清单，共 ${deleteList.length} 个文件）\n` +
        `2. delete_unwanted.bat（删除脚本）\n\n` +
        `${placeHint}。\n\n` +
        `脚本运行时会再次展示完整清单并请求确认，删除明细保存在 delete_log.txt。`
    );
});

// ================================================================
// 6. 初始化
// ================================================================
loadRecords();
renderRecords();
renderEmpty(manualResult, '请输入宽高后点击「判断」');
renderEmpty(dwgResult, '上传文件后自动批量解析');
dwgParseStatus.textContent = '💡 请确保本地解析服务运行在 http://localhost:5000';
dwgParseStatus.style.color = '#7e92b0';
btnStopBatch.disabled = true;