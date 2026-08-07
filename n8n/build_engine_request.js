const source = $json.raw_data;

if (!source || typeof source !== 'object') {
  throw new Error('Missing raw_data');
}

function unwrapData(value) {
  if (!value) return [];

  if (
    typeof value === 'object' &&
    Array.isArray(value.columns) &&
    Array.isArray(value.items)
  ) {
    return [value];
  }

  if (Array.isArray(value)) {
    return value.flatMap(unwrapData);
  }

  if (typeof value === 'object' && value.data) {
    return unwrapData(value.data);
  }

  return [];
}

function normalize(value) {
  return String(value ?? '')
    .replace(/\s+/g, '')
    .replace(/[（）]/g, char => char === '（' ? '(' : ')')
    .toLowerCase();
}

function matchesMetric(item, sheet, keywords) {
  const candidates = [
    item?.[0],
    sheet.metric,
    sheet.sheetName
  ].map(normalize);

  return keywords.some(keyword => {
    const expected = normalize(keyword);
    return candidates.some(candidate => candidate.includes(expected));
  });
}

function convertSheets(value, keywords = []) {
  const sheets = unwrapData(value);
  const convertedRows = [];

  for (const sheet of sheets) {
    const columns = Array.isArray(sheet.columns)
      ? sheet.columns
      : [];

    const items = Array.isArray(sheet.items)
      ? sheet.items
      : [];

    if (!columns.length || !items.length) continue;

    let selectedItems = items.filter(item =>
      Array.isArray(item) &&
      (
        keywords.length === 0 ||
        matchesMetric(item, sheet, keywords)
      )
    );

    // 只有一行时允许名称不完全一致，避免静默输出空数组
    if (selectedItems.length === 0 && items.length === 1) {
      selectedItems = items.filter(Array.isArray);
    }

    for (const item of selectedItems) {
      const row = {};

      for (let index = 0; index < columns.length; index++) {
        row[columns[index]] =
          index < item.length ? item[index] : null;
      }

      row._sheet_name = sheet.sheetName ?? null;
      row._metric = sheet.metric ?? item[0] ?? null;
      row._source = sheet.source ?? null;
      row._original_unit = sheet.original_unit ?? null;

      convertedRows.push(row);
    }
  }

  return convertedRows;
}

function normalizeDate(value) {
  const text = String(value ?? '').trim();
  if (/^\d{8}$/.test(text)) {
    return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
  }
  return text
    .replace(/年/g, '-')
    .replace(/月/g, '-')
    .replace(/日/g, '')
    .replace(/[/.]/g, '-');
}

function isDateKey(value) {
  return /^\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?$/.test(value) ||
    /^\d{8}$/.test(value);
}

function toPricePoints(value) {
  const points = [];

  if (Array.isArray(value?.observations)) {
    for (const observation of value.observations) {
      if (Array.isArray(observation) && observation.length >= 2) {
        points.push({
          date: normalizeDate(observation[0]),
          close: Number(observation[1])
        });
      }
    }
  }

  const directRows = Array.isArray(value)
    ? value
    : Array.isArray(value?.rows)
      ? value.rows
      : [];
  for (const row of directRows) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue;
    const date = row.date ?? row.trade_date ?? row['日期'] ?? row['交易日期'];
    const close = row.close ?? row.close_price ?? row['收盘价'];
    if (date != null && close != null) {
      points.push({date: normalizeDate(date), close: Number(close)});
    }
  }

  const rows = convertSheets(value, ['收盘价']);
  for (const row of rows) {
    const directDate = row.date ?? row.trade_date ?? row['日期'] ?? row['交易日期'];
    const directClose = row.close ?? row.close_price ?? row['收盘价'];
    if (directDate != null && directClose != null) {
      points.push({date: normalizeDate(directDate), close: Number(directClose)});
      continue;
    }

    for (const [key, cell] of Object.entries(row)) {
      if (isDateKey(key)) {
        points.push({date: normalizeDate(key), close: Number(cell)});
      }
    }
  }

  const unique = new Map();
  for (const point of points) {
    if (point.date && Number.isFinite(point.close)) {
      unique.set(point.date, point);
    }
  }
  return [...unique.values()].sort((left, right) =>
    left.date.localeCompare(right.date)
  );
}

const priceHistory = toPricePoints(source.price_history);
const analysisType = String($json.analysis_type ?? 'p1').trim().toLowerCase();

return {
  json: {
    symbol: $json.symbol,
    analysis_type: analysisType,
    raw_data: {
      price_history: {
        observations: priceHistory.map(point => [point.date, point.close])
      }
    },
    normalized_price_history: priceHistory,
    engine_request: {
      symbol: $json.symbol,
      price_history: priceHistory
    }
  }
};
