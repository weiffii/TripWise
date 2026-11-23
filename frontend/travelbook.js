/* TravelBook: локальный PDF без внешних сервисов и библиотек.
 * Страницы растеризуются Canvas, поэтому кириллица не зависит от PDF-шрифтов.
 * Текст в таком PDF нельзя выделять: для доступного текста остаётся экран маршрута.
 */
(() => {
  'use strict';
  const W = 1240, H = 1754, M = 88;
  const INK = '#393441', MUTED = '#777080', BG = '#fffdf9', LINE = '#e9e3e8';
  const SANS = '-apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif';
  const SERIF = 'Georgia, "Times New Roman", serif';
  const PALETTE = {morning: '#f8e7ce', afternoon: '#e0eee4', evening: '#e7e1f3', breakfast: '#f8e7ce', lunch: '#f4dfe9', dinner: '#e7e1f3'};
  const string = value => value == null ? '' : String(value);
  const nextFrame = () => new Promise(resolve => setTimeout(resolve, 0));

  function canvas() {
    const sheet = document.createElement('canvas');
    sheet.width = W; sheet.height = H;
    const ctx = sheet.getContext('2d', {alpha: false});
    if (!ctx) throw new Error('Браузер не поддерживает создание PDF. Попробуйте современный браузер.');
    ctx.fillStyle = BG; ctx.fillRect(0, 0, W, H);
    return {sheet, ctx};
  }
  function font(ctx, size, family = SANS, weight = '400') { ctx.font = `${weight} ${size}px ${family}`; }
  function text(ctx, value, x, y, size = 22, color = INK, family = SANS, weight = '400') {
    font(ctx, size, family, weight); ctx.fillStyle = color; ctx.textBaseline = 'top'; ctx.fillText(string(value), x, y);
  }
  function rule(ctx, y, x = M, width = W - M * 2) { ctx.fillStyle = LINE; ctx.fillRect(x, y, width, 1.5); }
  function rounded(ctx, x, y, width, height, radius, color) {
    ctx.beginPath(); ctx.roundRect(x, y, width, height, radius); ctx.fillStyle = color; ctx.fill();
  }
  // Длинные названия и даже одно длинное слово переносятся без обрезки.
  function wrap(ctx, value, width, size, family = SANS, weight = '400') {
    font(ctx, size, family, weight);
    const result = [];
    for (const paragraph of string(value).split('\n')) {
      let line = '';
      for (const word of paragraph.split(/[ \t]+/).filter(Boolean)) {
        if (ctx.measureText((line ? line + ' ' : '') + word).width <= width) { line += (line ? ' ' : '') + word; continue; }
        if (line) { result.push(line); line = ''; }
        for (const character of word) {
          if (line && ctx.measureText(line + character).width > width) { result.push(line); line = ''; }
          line += character;
        }
      }
      result.push(line);
    }
    return result;
  }
  function block(ctx, lines, x, y, size, color = INK, family = SANS, weight = '400', leading = 1.35) {
    lines.forEach((line, index) => text(ctx, line, x, y + index * size * leading, size, color, family, weight));
    return lines.length * size * leading;
  }
  function footer(ctx, page, total) {
    rule(ctx, H - 92);
    text(ctx, 'TripWise  /  Путешествие в вашем ритме', M, H - 69, 15, MUTED);
    text(ctx, `${String(page).padStart(2, '0')} / ${String(total).padStart(2, '0')}`, W - M - 78, H - 69, 15, MUTED);
  }
  function datesLabel(value) { return Array.isArray(value) ? value.map(string).join(' - ') : string(value); }
  async function illustration(source) {
    try {
      const url = new URL(source || '/assets/travel-landscape.svg', location.href);
      // Не отправляем данные маршрута стороннему ресурсу и не загрязняем Canvas CORS.
      if (url.origin !== location.origin) return null;
      return await new Promise(resolve => {
        const img = new Image(); const timeout = setTimeout(() => resolve(null), 4000);
        img.onload = () => { clearTimeout(timeout); resolve(img); };
        img.onerror = () => { clearTimeout(timeout); resolve(null); };
        img.src = url.href;
      });
    } catch { return null; }
  }
  function drawImage(ctx, image, x, y, width, height, radius = 28) {
    ctx.save(); ctx.beginPath(); ctx.roundRect(x, y, width, height, radius); ctx.clip();
    if (image) {
      const scale = Math.max(width / image.naturalWidth, height / image.naturalHeight);
      const iw = image.naturalWidth * scale, ih = image.naturalHeight * scale;
      ctx.drawImage(image, x + (width - iw) / 2, y + (height - ih) / 2, iw, ih);
    } else {
      const gradient = ctx.createLinearGradient(x, y, x + width, y + height);
      gradient.addColorStop(0, '#d7d4ea'); gradient.addColorStop(.6, '#f2d4cb'); gradient.addColorStop(1, '#f8e4bb');
      ctx.fillStyle = gradient; ctx.fillRect(x, y, width, height);
      ctx.fillStyle = '#fff1ce'; ctx.beginPath(); ctx.arc(x + width * .75, y + height * .35, 58, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#ada6bd'; ctx.beginPath(); ctx.moveTo(x, y + height); ctx.lineTo(x, y + height * .8);
      ctx.lineTo(x + width * .25, y + height * .4); ctx.lineTo(x + width * .55, y + height * .8);
      ctx.lineTo(x + width * .8, y + height * .55); ctx.lineTo(x + width, y + height); ctx.fill();
    }
    ctx.restore();
  }
  function cover(data, image, totalPages) {
    const {sheet, ctx} = canvas();
    const imageH = 765;
    drawImage(ctx, image, 0, 0, W, imageH, 0);
    const shade = ctx.createLinearGradient(0, 0, 0, imageH);
    shade.addColorStop(0, '#241b3d8c');
    shade.addColorStop(.34, '#241b3d12');
    shade.addColorStop(1, '#20192fed');
    ctx.fillStyle = shade; ctx.fillRect(0, 0, W, imageH);
    text(ctx, 'TRIPWISE  /  ПЕРСОНАЛЬНЫЙ ПЛАН ПУТЕШЕСТВИЯ', M, 72, 19, '#fff8ef', SANS, '600');
    text(ctx, 'Ваш TravelBook', M, 456, 102, '#fffdf9', SERIF);
    text(ctx, data.city, M + 5, 595, 47, '#fffdf9', SERIF);
    text(ctx, datesLabel(data.dates), M + 5, 667, 26, '#f3e7ec');

    text(ctx, 'В ЭТОЙ ПОЕЗДКЕ', M, 822, 19, '#775975', SANS, '600');
    text(ctx, 'Ваш маршрут в деталях', M, 857, 48, INK, SERIF);
    const gap = 20, cardW = (W - M * 2 - gap) / 2;
    const card = (column, y, tint, heading, value, hint) => {
      const x = M + (cardW + gap) * column;
      rounded(ctx, x, y, cardW, 164, 23, tint);
      text(ctx, heading, x + 29, y + 23, 16, MUTED, SANS, '600');
      const lines = wrap(ctx, value, cardW - 58, 36, SERIF);
      block(ctx, lines, x + 29, y + 55, 36, INK, SERIF, '400', 1.05);
      if (hint && lines.length < 2) text(ctx, hint, x + 29, y + 117, 18, MUTED);
    };
    card(0, 937, '#eee8f3', 'ДЛИТЕЛЬНОСТЬ', `${data.days.length} ${dayWord(data.days.length)}`, 'От первого до последнего дня');
    card(1, 937, '#f9ebd9', 'РАСХОДЫ ПО ПЛАНУ', data.cost || 'Не подтверждены', 'Ориентир на всю поездку');
    card(0, 1121, '#e6eee8', 'ПОСЕЩЕНИЯ', String(data.visits ?? '—'), 'Места для знакомства с городом');
    card(1, 1121, '#f4e6e8', 'ПЕРЕХОДЫ И ПОЕЗДКИ', String(data.transfers ?? '—'), 'Связи между остановками');

    const note = [data.costNote, ...(data.warnings || [])].filter(Boolean).join('\n');
    let bottom = 1321;
    if (note) {
      let size = 23;
      let lines = wrap(ctx, note, W - M * 2 - 60, size);
      while (lines.length * size * 1.35 > 198 && size > 17) {
        size--;
        lines = wrap(ctx, note, W - M * 2 - 60, size);
      }
      const height = lines.length * size * 1.35 + 61;
      if (bottom + height > H - 110) throw new Error('Детали бюджета не помещаются на обложке.');
      rounded(ctx, M, bottom, W - M * 2, height, 19, '#f3eff2');
      text(ctx, 'ДЕТАЛИ БЮДЖЕТА', M + 30, bottom + 16, 16, MUTED, SANS, '600');
      block(ctx, lines, M + 30, bottom + 43, size, INK);
      bottom += height + 32;
    }
    if (bottom < H - 176) {
      rule(ctx, bottom);
      text(ctx, 'Дальше — расписание и расходы на каждый день', M, bottom + 24, 28, MUTED, SERIF);
    }
    footer(ctx, 1, totalPages);
    return sheet;
  }
  function dayWord(count) {
    const mod100 = count % 100, mod10 = count % 10;
    return mod100 >= 11 && mod100 <= 14 ? 'дней путешествия' : mod10 === 1 ? 'день путешествия' : mod10 >= 2 && mod10 <= 4 ? 'дня путешествия' : 'дней путешествия';
  }
  const COL = [M + 16, M + 193, M + 735, M + 896];
  const WIDTHS = [157, 520, 143, 145];
  function rowLayout(ctx, row, size) {
    const values = [row.time, row.action, row.duration, row.cost];
    const cells = values.map((value, index) => wrap(ctx, value || (index > 1 ? 'Нет данных' : ''), WIDTHS[index], size));
    const category = row.category ? wrap(ctx, row.category, WIDTHS[1], size * .82) : [];
    const actionHeight = cells[1].length * size * 1.3 + (category.length ? size * .25 + category.length * size * .82 * 1.3 : 0);
    const height = Math.max(actionHeight, ...cells.filter((_, index) => index !== 1).map(lines => lines.length * size * 1.3)) + size * 1.1;
    return {cells, category, height};
  }
  function dayLayout(ctx, day, size) {
    const sections = day.sections.filter(section => section.rows.length).map(section => ({...section, rows: section.rows.map(row => ({...row, layout: rowLayout(ctx, row, size)}))}));
    const warningLines = (day.warnings || []).flatMap(warning => wrap(ctx, warning, W - M * 2 - 32, size * .9));
    const sectionHeight = sections.reduce((sum, section) => sum + size * 3.7 + section.rows.reduce((height, row) => height + row.layout.height, 0) + size * .85, 0);
    const totalLines = day.total ? wrap(ctx, day.total, W - M * 2 - 44, size * 1.18) : [];
    const tailHeight = (totalLines.length ? totalLines.length * size * 1.55 + size * 2 : 0) + (warningLines.length ? warningLines.length * size * 1.22 + size * 2.4 : 0);
    return {sections, warningLines, totalLines, height: sectionHeight + tailHeight};
  }
  function dayPage(day, index, city, totalPages) {
    const {sheet, ctx} = canvas();
    text(ctx, 'TRIPWISE  /  ВАШ TRAVELBOOK', M, 67, 15, MUTED);
    const titleLines = wrap(ctx, day.label || `День ${index + 1}`, W - M * 2, 44, SERIF);
    const titleH = block(ctx, titleLines, M, 119, 44, INK, SERIF, '400', 1.15);
    const cityLines = wrap(ctx, city, W - M * 2, 20);
    const cityY = 130 + titleH;
    block(ctx, cityLines, M, cityY, 20, MUTED);
    let top = cityY + cityLines.length * 27 + 23;
    ['ВРЕМЯ', 'ЧТО ДЕЛАЕМ', 'ДЛИТЕЛЬНОСТЬ', 'СТОИМОСТЬ'].forEach((label, i) => text(ctx, label, COL[i], top, 13, MUTED, SANS, '600'));
    top += 31; rule(ctx, top); top += 12;
    const available = H - 125 - top;
    // 23px ≈ 11pt на A4; нижний предел15px ≈7.2pt. Ничего не удаляем ради вёрстки.
    let size = 23, layout = dayLayout(ctx, day, size);
    while (layout.height > available && size > 15) { size -= .5; layout = dayLayout(ctx, day, size); }
    if (layout.height > available) throw new Error(`«${day.label || `День ${index + 1}`}»: расписание слишком длинное для одной читаемой страницы A4. Сократите описания или число событий и повторите экспорт. Ни одна строка не была удалена.`);
    let y = top;
    if (!layout.sections.length) { text(ctx, 'На этот день нет событий в сохранённом плане.', M, y + 25, 22, MUTED); y += 88; }
    for (const section of layout.sections) {
      rounded(ctx, M, y, W - M * 2, size * 2.2, 10, PALETTE[section.tone] || '#eee8f3');
      text(ctx, section.title || 'Программа дня', M + 16, y + size * .52, size * 1.03, INK, SANS, '600');
      y += size * 2.7;
      for (const row of section.rows) {
        const rowY = y + size * .3;
        row.layout.cells.forEach((lines, column) => block(ctx, lines, COL[column], rowY, size, column === 1 ? INK : MUTED, SANS, column === 1 ? '500' : '400', 1.3));
        if (row.layout.category.length) block(ctx, row.layout.category, COL[1], rowY + row.layout.cells[1].length * size * 1.3 + size * .25, size * .82, MUTED, SANS, '400', 1.3);
        y += row.layout.height; rule(ctx, y);
      }
      y += size * 1.85;
    }
    if (layout.totalLines.length) {
      const height = layout.totalLines.length * size * 1.55 + size * 1.4;
      rounded(ctx, M, y, W - M * 2, height, 12, '#efe9f4');
      block(ctx, layout.totalLines, M + 22, y + size * .7, size * 1.18, INK, SANS, '600', 1.31);
      y += height + size * .6;
    }
    if (layout.warningLines.length) {
      const height = layout.warningLines.length * size * 1.22 + size * 2;
      rounded(ctx, M, y, W - M * 2, height, 12, '#faf0df');
      block(ctx, layout.warningLines, M + 16, y + size, size * .9, '#78634a', SANS, '400', 1.355);
    }
    footer(ctx, index + 2, totalPages);
    return sheet;
  }
  async function jpeg(sheet) {
    const blob = await new Promise(resolve => sheet.toBlob(resolve, 'image/jpeg', .92));
    if (!blob) throw new Error('Браузеру не хватило памяти для страницы PDF. Закройте лишние вкладки и повторите.');
    const bytes = new Uint8Array(await blob.arrayBuffer());
    sheet.width = 1; sheet.height = 1;
    return bytes;
  }
  function utf16(value) {
    let hex = 'FEFF';
    for (let i = 0; i < value.length; i++) hex += value.charCodeAt(i).toString(16).padStart(4, '0');
    return '<' + hex + '>';
  }
  function pdf(images, title) {
    const encoder = new TextEncoder(), parts = [], offsets = [0]; let length = 0;
    const append = value => { const bytes = typeof value === 'string' ? encoder.encode(value) : value; parts.push(bytes); length += bytes.length; };
    const object = (id, body) => { offsets[id] = length; append(`${id} 0 obj\n`); append(body); append('\nendobj\n'); };
    append('%PDF-1.4\n%TripWise\n');
    object(1, '<< /Type /Catalog /Pages 2 0 R >>');
    object(2, `<< /Type /Pages /Count ${images.length} /Kids [${images.map((_, i) => `${3 + i * 3} 0 R`).join(' ')}] >>`);
    images.forEach((bytes, index) => {
      const id = 3 + index * 3, imageId = id + 2;
      object(id, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.276 841.89] /Resources << /XObject << /Im0 ${imageId} 0 R >> >> /Contents ${id + 1} 0 R >>`);
      const stream = 'q\n595.276 0 0 841.89 0 0 cm\n/Im0 Do\nQ\n';
      object(id + 1, `<< /Length ${encoder.encode(stream).length} >>\nstream\n${stream}endstream`);
      offsets[imageId] = length;
      append(`${imageId} 0 obj\n<< /Type /XObject /Subtype /Image /Width ${W} /Height ${H} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${bytes.length} >>\nstream\n`);
      append(bytes); append('\nendstream\nendobj\n');
    });
    const infoId = 3 + images.length * 3;
    object(infoId, `<< /Title ${utf16(title)} /Author (TripWise) /Creator (TripWise TravelBook) >>`);
    const xref = length;
    append(`xref\n0 ${infoId + 1}\n0000000000 65535 f \n`);
    for (let id = 1; id <= infoId; id++) append(`${String(offsets[id]).padStart(10, '0')} 00000 n \n`);
    append(`trailer\n<< /Size ${infoId + 1} /Root 1 0 R /Info ${infoId} 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
    return new Blob(parts, {type: 'application/pdf'});
  }
  function normalize(input) {
    if (!input || typeof input !== 'object' || !Array.isArray(input.days)) throw new Error('Для TravelBook нужен маршрут с расписанием по дням.');
    if (!input.days.length) throw new Error('В маршруте пока нет дней для TravelBook.');
    if (input.days.length > 60) throw new Error('За один экспорт можно сохранить не больше 60 дней.');
    const warnings = value => Array.isArray(value) ? value.map(string).filter(Boolean) : value ? [string(value)] : [];
    return {...input, city: string(input.city || 'Ваше путешествие'), cost: string(input.cost), costNote: string(input.costNote), warnings: warnings(input.warnings), days: input.days.map((day, index) => {
      if (!day || !Array.isArray(day.sections)) throw new Error(`Нет расписания для дня ${index + 1}.`);
      return {...day, label: string(day.label), warnings: warnings(day.warnings), sections: day.sections.map(section => {
        if (!section || !Array.isArray(section.rows)) throw new Error(`Некорректный раздел расписания в дне ${index + 1}.`);
        return {...section, title: string(section.title), rows: section.rows.map(row => {
          if (!row || typeof row !== 'object') throw new Error('Некорректная строка расписания.');
          return Object.fromEntries(['time', 'action', 'category', 'duration', 'cost'].map(key => [key, string(row[key])]));
        })};
      })};
    })};
  }
  async function createBlob(input) {
    const data = normalize(input);
    if (document.fonts?.ready) await document.fonts.ready;
    const image = await illustration(data.illustration);
    const pageCount = data.days.length + 1;
    const images = [await jpeg(cover(data, image, pageCount))];
    for (let i = 0; i < data.days.length; i++) {
      await nextFrame(); images.push(await jpeg(dayPage(data.days[i], i, data.city, pageCount)));
    }
    return {blob: pdf(images, `Ваш TravelBook - ${data.city}`), pageCount, filename: `TripWise-TravelBook-${data.city.replace(/[^\p{L}\p{N} _-]/gu, '').slice(0, 60) || 'trip'}.pdf`};
  }
  async function download(input) {
    const result = await createBlob(input);
    const url = URL.createObjectURL(result.blob);
    const link = document.createElement('a'); link.href = url; link.download = result.filename;
    document.body.append(link); link.click(); link.remove();
    // Даём Safari время прочитать Blob; потом освобождаем объектный URL.
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    return result;
  }
  window.TripWiseTravelBook = Object.freeze({download, createBlob});
})();
