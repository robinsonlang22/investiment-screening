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

const priceHistory = convertSheets(
  source.price_history,
  ['收盘价']
);

const marginHistory = convertSheets(
  source.margin_history,
  ['融资余额']
);

const marketCap = convertSheets(
  source.market_cap,
  ['自由流通市值']
);

const marketMarginHistory = convertSheets(
  source.market_margin_history,
  ['融资余额', '融资余额(合计)']
);

return {
  json: {
    symbol: $json.symbol,
    raw_data: {
      price_history: priceHistory,
      margin_history: marginHistory,
      market_cap: marketCap,
      market_margin_history: marketMarginHistory
    },
    // The research request is explicitly for forward-adjusted daily prices.
    // Preserve that contract when the MCP table itself omits adjustment
    // metadata instead of forcing the Python adapter to guess from values.
    adapter_options: {
      price_history: {
        adjustment: 'forward'
      }
    }
  }
};
