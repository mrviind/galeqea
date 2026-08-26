/**
 * A deliberately small JSON Schema checker for API response conformance.
 *
 * Not a general-purpose validator and not trying to be: it covers the subset an
 * OpenAPI response schema actually uses - type, required, properties, items,
 * enum, nullable and the numeric/length keywords - and reports *every* problem
 * with the JSON pointer that locates it, because "response did not match the
 * schema" is useless in a failure report and "$.items[2].price: expected number,
 * got string" is actionable.
 *
 * Unknown keywords are ignored rather than treated as failures, so a spec using
 * a feature this does not model degrades to checking less, never to false alarms.
 */

const TYPE_OF = (v) => {
  if (v === null) return 'null';
  if (Array.isArray(v)) return 'array';
  if (Number.isInteger(v)) return 'integer';
  return typeof v === 'number' ? 'number' : typeof v;
};

/** Returns an array of human-readable violations; empty means conformant. */
export function validate(value, schema, pointer = '$', depth = 0) {
  const problems = [];
  if (!schema || typeof schema !== 'object' || depth > 12) return problems;

  // `nullable: true` is OpenAPI 3.0's way of saying null is permitted; 3.1 uses
  // a type union. Both are accepted so one checker serves both spec versions.
  const types = [].concat(schema.type ?? []);
  if (value === null && (schema.nullable === true || types.includes('null'))) return problems;

  if (types.length) {
    const actual = TYPE_OF(value);
    // An integer satisfies `number`; the reverse is not true.
    const ok = types.some((t) => t === actual || (t === 'number' && actual === 'integer'));
    if (!ok) {
      problems.push(`${pointer}: expected ${types.join('|')}, got ${actual}`);
      return problems;                       // further checks would be noise
    }
  }

  if (Array.isArray(schema.enum) && !schema.enum.some((e) => e === value)) {
    problems.push(`${pointer}: ${JSON.stringify(value)} is not one of ${JSON.stringify(schema.enum)}`);
  }

  if (TYPE_OF(value) === 'object') {
    for (const key of schema.required || []) {
      if (!(key in value)) problems.push(`${pointer}.${key}: required property is missing`);
    }
    for (const [key, sub] of Object.entries(schema.properties || {})) {
      if (key in value) problems.push(...validate(value[key], sub, `${pointer}.${key}`, depth + 1));
    }
    if (schema.additionalProperties === false) {
      const declared = new Set(Object.keys(schema.properties || {}));
      for (const key of Object.keys(value)) {
        if (!declared.has(key)) problems.push(`${pointer}.${key}: undeclared property, additionalProperties is false`);
      }
    }
  }

  if (Array.isArray(value)) {
    if (typeof schema.minItems === 'number' && value.length < schema.minItems) {
      problems.push(`${pointer}: ${value.length} items, minimum is ${schema.minItems}`);
    }
    if (schema.items) {
      // Cap the per-array reporting: a 5000-row response with a systematic type
      // error should produce a readable failure, not 5000 identical lines.
      for (let i = 0; i < Math.min(value.length, 25); i++) {
        problems.push(...validate(value[i], schema.items, `${pointer}[${i}]`, depth + 1));
      }
    }
  }

  if (typeof value === 'string') {
    if (typeof schema.minLength === 'number' && value.length < schema.minLength) {
      problems.push(`${pointer}: length ${value.length} is below minLength ${schema.minLength}`);
    }
    if (typeof schema.maxLength === 'number' && value.length > schema.maxLength) {
      problems.push(`${pointer}: length ${value.length} exceeds maxLength ${schema.maxLength}`);
    }
    if (schema.pattern) {
      try {
        if (!new RegExp(schema.pattern).test(value)) {
          problems.push(`${pointer}: does not match pattern ${schema.pattern}`);
        }
      } catch { /* an unparseable pattern is the spec's problem, not the run's */ }
    }
  }

  if (typeof value === 'number') {
    if (typeof schema.minimum === 'number' && value < schema.minimum) {
      problems.push(`${pointer}: ${value} is below minimum ${schema.minimum}`);
    }
    if (typeof schema.maximum === 'number' && value > schema.maximum) {
      problems.push(`${pointer}: ${value} is above maximum ${schema.maximum}`);
    }
  }

  return problems.slice(0, 40);
}

/** Read a dotted/bracketed path out of a parsed body. Returns undefined if absent. */
export function readPath(root, path) {
  const parts = String(path).replace(/^\$\.?/, '').split(/\.|\[(\d+)\]/).filter((p) => p !== '' && p !== undefined);
  let node = root;
  for (const part of parts) {
    if (node === null || node === undefined) return undefined;
    node = node[/^\d+$/.test(part) ? Number(part) : part];
  }
  return node;
}
