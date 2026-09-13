//! Dumps the JSON Schema for `rookie_cookies::report`'s wire types.
//!
//! `report_core.rs` is already the frozen cross-engine report contract; this
//! binary is the "one schema" workstream B (issue #241) generates typed
//! Python/Node DTOs from, so the schema and the Rust types it's generated
//! from can never drift apart the way Node's hand-duplicated `#[napi(object)]`
//! structs already have. Run with `cargo run --bin generate-dto-schema
//! --features dto-schema` from `rookie-rs/`; writes to
//! `../schema/report-dto.schema.json` by default, or to the path given as
//! the first argument. `dto-schema` is off by default and gates the
//! `JsonSchema` derives themselves, not just this binary, so the CLI and
//! the Python/Node bindings don't pull in `schemars` for a capability they
//! never call.

use rookie_cookies::enums::Cookie;
use rookie_cookies::report::{
  BrowserCapabilitiesDescriptor, BrowserDescriptor, CookieSourceDescriptor, CookieSourceIdentity,
  ExtractionIssue, ExtractionReport, ExtractionStats, ProfileDescriptor, ProfileExtraction,
  ProfileIdentity, ReportStats, SourceExtraction,
};
use schemars::generate::{SchemaGenerator, SchemaSettings};
use serde_json::{json, Map, Value};
use std::env;
use std::fs;
use std::path::PathBuf;

/// Top-level shapes a binding generates a typed class for. Every other type
/// referenced from these (identifiers, nested descriptors) lands in
/// `definitions` because `SchemaGenerator::subschema_for` collects them
/// there automatically.
fn root_definitions(generator: &mut SchemaGenerator) -> Map<String, Value> {
  let mut roots = Map::new();
  roots.insert(
    "Cookie".to_owned(),
    schema_value(generator.subschema_for::<Cookie>()),
  );
  roots.insert(
    "BrowserDescriptor".to_owned(),
    schema_value(generator.subschema_for::<BrowserDescriptor>()),
  );
  roots.insert(
    "ProfileDescriptor".to_owned(),
    schema_value(generator.subschema_for::<ProfileDescriptor>()),
  );
  roots.insert(
    "ExtractionReport".to_owned(),
    schema_value(generator.subschema_for::<ExtractionReport>()),
  );
  // Referenced from the roots above but also useful as standalone request
  // building blocks for a binding's typed layer.
  roots.insert(
    "ProfileExtraction".to_owned(),
    schema_value(generator.subschema_for::<ProfileExtraction>()),
  );
  roots.insert(
    "SourceExtraction".to_owned(),
    schema_value(generator.subschema_for::<SourceExtraction>()),
  );
  roots.insert(
    "ExtractionIssue".to_owned(),
    schema_value(generator.subschema_for::<ExtractionIssue>()),
  );
  roots.insert(
    "CookieSourceDescriptor".to_owned(),
    schema_value(generator.subschema_for::<CookieSourceDescriptor>()),
  );
  roots.insert(
    "CookieSourceIdentity".to_owned(),
    schema_value(generator.subschema_for::<CookieSourceIdentity>()),
  );
  roots.insert(
    "ProfileIdentity".to_owned(),
    schema_value(generator.subschema_for::<ProfileIdentity>()),
  );
  roots.insert(
    "BrowserCapabilitiesDescriptor".to_owned(),
    schema_value(generator.subschema_for::<BrowserCapabilitiesDescriptor>()),
  );
  roots.insert(
    "ExtractionStats".to_owned(),
    schema_value(generator.subschema_for::<ExtractionStats>()),
  );
  roots.insert(
    "ReportStats".to_owned(),
    schema_value(generator.subschema_for::<ReportStats>()),
  );
  roots
}

fn schema_value(schema: schemars::Schema) -> Value {
  let mut value = schema.to_value();
  normalize_schemars_08_output(&mut value);
  value
}

/// Keep the checked-in schema byte-for-byte stable across the schemars 0.8 to
/// 1.x migration. Schemars 1.x changed JSON object ordering, preserves source
/// line wrapping in doc comments, emits integer upper bounds, and preserves
/// declaration order in `required`; none of those serialization differences
/// changes this project's frozen wire contract.
fn normalize_schemars_08_output(schema: &mut Value) {
  let Some(object) = schema.as_object_mut() else {
    if let Some(values) = schema.as_array_mut() {
      for value in values {
        normalize_schemars_08_output(value);
      }
    }
    return;
  };

  for value in object.values_mut() {
    normalize_schemars_08_output(value);
  }

  if let Some(description) = object.get("description").and_then(Value::as_str) {
    let normalized = description
      .split("\n\n")
      .map(|paragraph| paragraph.lines().collect::<Vec<_>>().join(" "))
      .collect::<Vec<_>>()
      .join("\n\n");
    object.insert("description".to_owned(), Value::String(normalized));
  }

  if let Some(required) = object.get_mut("required").and_then(Value::as_array_mut) {
    required.sort_by(|left, right| left.as_str().cmp(&right.as_str()));
  }

  let is_integer = match object.get("type") {
    Some(Value::String(kind)) => kind == "integer",
    Some(Value::Array(kinds)) => kinds.iter().any(|kind| kind == "integer"),
    _ => false,
  };
  if is_integer {
    // Schemars 0.8 represented unsigned Rust integers with a 0.0 lower bound
    // and no machine-width upper bound. Preserve that published schema.
    object.remove("maximum");
    if matches!(object.get("minimum"), Some(Value::Number(number)) if number.as_u64() == Some(0)) {
      object.insert(
        "minimum".to_owned(),
        Value::Number(serde_json::Number::from_f64(0.0).expect("zero is finite")),
      );
    }
  }

  // This is the field order of schemars 0.8's serializable SchemaObject and
  // validation structs. serde_json's preserve_order feature makes it part of
  // the pretty-printed artifact, so reproduce it explicitly.
  const SCHEMARS_08_KEY_ORDER: &[&str] = &[
    "$id",
    "title",
    "description",
    "default",
    "deprecated",
    "readOnly",
    "writeOnly",
    "examples",
    "type",
    "format",
    "enum",
    "const",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "if",
    "then",
    "else",
    "multipleOf",
    "maximum",
    "exclusiveMaximum",
    "minimum",
    "exclusiveMinimum",
    "maxLength",
    "minLength",
    "pattern",
    "items",
    "additionalItems",
    "maxItems",
    "minItems",
    "uniqueItems",
    "contains",
    "maxProperties",
    "minProperties",
    "required",
    "properties",
    "patternProperties",
    "additionalProperties",
    "propertyNames",
    "$ref",
  ];

  let mut reordered = Map::with_capacity(object.len());
  for key in SCHEMARS_08_KEY_ORDER {
    if let Some(value) = object.remove(*key) {
      reordered.insert((*key).to_owned(), value);
    }
  }
  reordered.append(object);
  *object = reordered;
}

fn reorder_properties(schema: &mut Value, preferred_order: &[&str]) {
  let Some(properties) = schema.get_mut("properties").and_then(Value::as_object_mut) else {
    return;
  };
  let mut reordered = Map::with_capacity(properties.len());
  for name in preferred_order {
    if let Some(value) = properties.remove(*name) {
      reordered.insert((*name).to_owned(), value);
    }
  }
  reordered.append(properties);
  *properties = reordered;
}

/// Recursively removes `enum`/`const` constraints from `schema` and every
/// nested schema reachable through `properties`, `items`, and
/// `additionalProperties`. See the call site in `main` for why.
fn strip_enum_and_const(schema: &mut Value) {
  let Some(object) = schema.as_object_mut() else {
    return;
  };
  object.remove("enum");
  object.remove("const");
  if let Some(properties) = object.get_mut("properties").and_then(Value::as_object_mut) {
    for property in properties.values_mut() {
      strip_enum_and_const(property);
    }
  }
  if let Some(items) = object.get_mut("items") {
    strip_enum_and_const(items);
  }
  if let Some(additional) = object.get_mut("additionalProperties") {
    strip_enum_and_const(additional);
  }
}

fn main() {
  let mut generator = SchemaSettings::draft07().into_generator();
  let roots = root_definitions(&mut generator);

  let mut definitions: Map<String, Value> = generator.take_definitions(true);
  for schema in definitions.values_mut() {
    normalize_schemars_08_output(schema);
  }
  // Schemars 1.x reorders fields around these custom inlined identifier
  // schemas. Restore declaration order so the frozen artifact does not churn.
  for type_name in ["CookieSourceDescriptor", "CookieSourceIdentity"] {
    if let Some(schema) = definitions.get_mut(type_name) {
      reorder_properties(
        schema,
        &["role", "format", "path", "path_lossy", "precedence"],
      );
    }
  }
  // Open identifiers are validated snake_case strings, deliberately not a
  // closed enum -- see report_core.rs. Strip any `enum`/`const` constraint
  // schemars may have inferred so a generated DTO class stays forward
  // compatible with values this build has never heard of. Every open
  // identifier today is a `#[serde(transparent)]` newtype with a custom,
  // inlined lexical string schema. A shallow strip would happen to be enough
  // right now, but strip recursively anyway so a future report field backed
  // by a real Rust `enum`, or a nested inlined type, can't silently smuggle a
  // closed `enum`/`const` constraint into the generated schema. Lexical
  // `pattern`/length constraints are intentionally retained.
  for schema in definitions.values_mut() {
    strip_enum_and_const(schema);
  }

  let document = json!({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$comment": "Generated by `cargo run --bin generate-dto-schema` from rookie-rs/src/browser/report_core.rs. Do not hand-edit -- see that file for the source of truth and schema/README.md for regeneration instructions.",
    "title": "rookie-cookies report DTO schema",
    "roots": roots,
    "definitions": definitions,
  });

  let output_path = env::args().nth(1).map(PathBuf::from).unwrap_or_else(|| {
    // CARGO_MANIFEST_DIR (set at compile time) rather than a CWD-relative
    // path, so the default works regardless of which directory `cargo run`
    // was invoked from -- only explicit invocations from `rookie-rs/` (see
    // the module doc above) got this right before.
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../schema/report-dto.schema.json")
  });
  let rendered = serde_json::to_string_pretty(&document).expect("document serializes to JSON");
  fs::write(&output_path, rendered + "\n")
    .unwrap_or_else(|error| panic!("failed to write {}: {error}", output_path.display()));
  eprintln!("wrote {}", output_path.display());
}

#[cfg(test)]
mod tests {
  use super::*;

  fn property<'a>(roots: &'a Map<String, Value>, type_name: &str, field: &str) -> &'a Value {
    &roots[type_name]["properties"][field]
  }

  #[test]
  fn generated_definitions_preserve_open_and_opaque_identifier_constraints() {
    let mut generator = SchemaSettings::draft07().into_generator();
    let _roots = root_definitions(&mut generator);
    let definitions: Map<String, Value> = generator.take_definitions(true);
    let open = property(&definitions, "BrowserDescriptor", "id");
    assert_eq!(open["type"], "string");
    assert_eq!(open["minLength"], 1);
    assert_eq!(open["pattern"], "^[a-z]");
    assert_eq!(open["not"]["type"], "string");
    assert_eq!(open["not"]["pattern"], "[^a-z0-9_]");
    assert!(open.get("enum").is_none(), "open vocabulary must stay open");

    let optional_open = property(&definitions, "ExtractionIssue", "browser_id");
    assert_eq!(optional_open["type"], json!(["string", "null"]));
    assert_eq!(optional_open["not"]["type"], "string");
    assert_eq!(optional_open["not"]["pattern"], "[^a-z0-9_]");

    let opaque = property(&definitions, "ProfileIdentity", "profile_id");
    assert_eq!(opaque["type"], "string");
    assert_eq!(opaque["minLength"], 64);
    assert_eq!(opaque["maxLength"], 64);
    assert_eq!(opaque["pattern"], "^[0-9a-f]{64}$");

    let optional_opaque = property(&definitions, "ExtractionIssue", "installation_id");
    assert_eq!(optional_opaque["type"], json!(["string", "null"]));
    assert_eq!(optional_opaque["minLength"], 64);
    assert_eq!(optional_opaque["maxLength"], 64);
    assert_eq!(optional_opaque["pattern"], "^[0-9a-f]{64}$");
  }
}
