const rawOutput = $json.output;

if (typeof rawOutput === 'object' && rawOutput !== null) {
  return {
    json: rawOutput
  };
}

if (typeof rawOutput !== 'string') {
  throw new Error(`Unexpected output type: ${typeof rawOutput}`);
}

const cleaned = rawOutput
  .trim()
  .replace(/^```json\s*/i, '')
  .replace(/^```\s*/i, '')
  .replace(/\s*```$/, '');

let parsed;

try {
  parsed = JSON.parse(cleaned);
} catch (error) {
  throw new Error(`Invalid Agent JSON: ${error.message}`);
}

return {
  json: parsed
};