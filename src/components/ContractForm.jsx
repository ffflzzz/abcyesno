import React, { useState, useEffect } from "react";

// Generic contract input form (L2). Data-driven from manifest.input_schema:
// no per-workflow branch. Renders text/textarea/select/number controls per
// each property's x-ui.control and validates required/enum client-side.
function validate(schema, values) {
  const errors = {};
  const props = (schema && schema.properties) || {};
  const required = (schema && schema.required) || [];
  for (const key of required) {
    const v = values[key];
    if (v === undefined || v === null || (typeof v === "string" && !v.trim())) {
      errors[key] = "必填";
    }
  }
  for (const key of Object.keys(props)) {
    const def = props[key];
    const v = values[key];
    if (def.enum && v !== undefined && v !== "" && !def.enum.includes(v)) {
      errors[key] = "取值非法";
    }
  }
  return errors;
}

function renderControl(key, def, value, onChange, error) {
  const ui = def.x_ui || {};
  const control = ui.control || "text";
  const label = ui.label || key;
  const placeholder = ui.placeholder || "";
  const common = {
    id: key,
    value: value ?? "",
    onChange: (e) => onChange(key, e.target.value),
  };
  let input;
  if (control === "textarea") {
    input = <textarea {...common} rows={4} placeholder={placeholder} />;
  } else if (control === "select") {
    input = (
      <select {...common} value={value ?? ""}>
        <option value="">请选择</option>
        {(def.enum || []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  } else if (control === "number") {
    input = <input type="number" {...common} placeholder={placeholder} />;
  } else {
    input = <input type="text" {...common} placeholder={placeholder} />;
  }
  return (
    <div className="contract-field" key={key}>
      <label className="contract-label" htmlFor={key}>
        {label}
      </label>
      {input}
      {error ? <div className="contract-error">{error}</div> : null}
    </div>
  );
}

export default function ContractForm({ manifest, onRun, onExit, disabled }) {
  const schema =
    (manifest && manifest.input_schema) || {
      type: "object",
      properties: { input: { type: "string", x_ui: { control: "textarea", label: "输入" } } },
      required: ["input"],
    };
  const props = schema.properties || {};
  const [values, setValues] = useState({});
  const [errors, setErrors] = useState({});

  useEffect(() => {
    const seed = {};
    for (const key of Object.keys(props)) {
      if (props[key].default !== undefined) seed[key] = props[key].default;
    }
    setValues(seed);
    setErrors({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manifest && manifest.id]);

  function setVal(key, v) {
    setValues((prev) => ({ ...prev, [key]: v }));
  }

  function submit() {
    const errs = validate(schema, values);
    setErrors(errs);
    if (Object.keys(errs).length) return;
    const out = {};
    for (const k of Object.keys(props)) {
      const v = values[k];
      if (v !== undefined && v !== "") out[k] = v;
    }
    if (onRun) onRun(manifest, out);
  }

  return (
    <div className="contract-form">
      <div className="contract-form-head">
        <div>
          <div className="contract-form-title">{manifest.name}</div>
          <div className="contract-form-desc">{manifest.description}</div>
        </div>
        {onExit && (
          <button className="contract-exit" onClick={onExit} title="退出工作流模式">
            普通对话
          </button>
        )}
      </div>
      <div className="contract-fields">
        {Object.keys(props).map((key) => renderControl(key, props[key], values[key], setVal, errors[key]))}
      </div>
      <button className="contract-submit primary" onClick={submit} disabled={disabled}>
        运行 {manifest.name}
      </button>
    </div>
  );
}
