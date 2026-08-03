{{/*
Fail-fast validation for Abbenay configuration.
Detects legacy values from pre-2026.4 chart versions and requires explicit
migration to the new providers-based API.
*/}}

{{- define "apme.abbenay.validateLegacyKeys" -}}
{{- if .Values.abbenay.enabled }}

{{- if hasKey .Values.abbenay "apiKeys" }}
{{- fail `

BREAKING CHANGE: abbenay.apiKeys has been removed.

Migrate to the new providers map in your values file:

  abbenay:
    providers:
      openrouter:
        engine: openrouter
        apiKey: "<your-openrouter-key>"
        models:
          anthropic/claude-sonnet-4: {}
      anthropic:
        engine: anthropic
        apiKey: "<your-anthropic-key>"
        models:
          claude-sonnet-4-20250514: {}

See docs/guides/ABBENAY_AI.md for full migration examples.
` }}
{{- end }}

{{- if hasKey .Values.abbenay "openrouterModels" }}
{{- fail `

BREAKING CHANGE: abbenay.openrouterModels has been removed.

Migrate to the new providers map:

  abbenay:
    providers:
      openrouter:
        engine: openrouter
        apiKey: "<your-key>"
        models:
          anthropic/claude-sonnet-4: {}
          anthropic/claude-opus-4.6: {}

Models are now specified per-provider under providers.<name>.models.
` }}
{{- end }}

{{- if hasKey .Values.abbenay "vertexAnthropic" }}
{{- fail `

BREAKING CHANGE: abbenay.vertexAnthropic has been removed.

Migrate to the new providers map:

  abbenay:
    providers:
      vertex-claude:
        engine: vertex-anthropic
        models:
          claude-sonnet-4@20250514: {}
    gcp:
      project: "<your-project>"
      location: us-east5
      serviceAccountKey: "<json>"   # or use existingSecret

See docs/guides/ABBENAY_AI.md for full examples.
` }}
{{- end }}

{{- if hasKey .Values.abbenay.gcp "existingGcpSecret" }}
{{- fail `

BREAKING CHANGE: abbenay.gcp.existingGcpSecret has been renamed to abbenay.gcp.existingSecret.

Update your values file:

  abbenay:
    gcp:
      existingSecret: "your-gcp-secret-name"   # was: existingGcpSecret

See docs/guides/ABBENAY_AI.md for the updated schema.
` }}
{{- end }}

{{- if hasKey .Values.abbenay.gcp "region" }}
{{- fail `

BREAKING CHANGE: abbenay.gcp.region has been renamed to abbenay.gcp.location.

Update your values file:

  abbenay:
    gcp:
      location: us-east5   # was: region

This aligns with Google Cloud's "location" terminology for Vertex AI endpoints.
` }}
{{- end }}

{{- end }}
{{- end -}}

{{/*
Validate provider names produce safe env var identifiers.
Allowed: lowercase alphanumeric and hyphens only (no dots, underscores,
uppercase, or multi-byte characters). This prevents collisions after the
upper(replace("-", "_", name)) normalization used in env var names.
*/}}
{{- define "apme.abbenay.validateProviderNames" -}}
{{- range $name, $_ := .Values.abbenay.providers }}
  {{- if not (regexMatch "^[a-z][a-z0-9-]*$" $name) }}
  {{- fail (printf "abbenay.providers key %q is invalid: provider names must match ^[a-z][a-z0-9-]*$ (lowercase, start with letter, only alphanumeric and hyphens)" $name) }}
  {{- end }}
  {{- if gt (len $name) 40 }}
  {{- fail (printf "abbenay.providers key %q exceeds 40 characters; env var names would be unwieldy" $name) }}
  {{- end }}
{{- end }}
{{- end -}}

{{/*
Validate apiKeySecret references have both required fields (.name and .key).
A half-populated reference renders successfully but fails at apply time.
*/}}
{{- define "apme.abbenay.validateApiKeySecrets" -}}
{{- range $name, $provider := .Values.abbenay.providers }}
  {{- if $provider.apiKeySecret }}
    {{- if not $provider.apiKeySecret.name }}
    {{- fail (printf "abbenay.providers.%s.apiKeySecret.name is required (the Kubernetes Secret name)" $name) }}
    {{- end }}
    {{- if not $provider.apiKeySecret.key }}
    {{- fail (printf "abbenay.providers.%s.apiKeySecret.key is required (the key within the Secret)" $name) }}
    {{- end }}
  {{- end }}
{{- end }}
{{- end -}}

{{/*
Validate provider schema: every provider must have engine and at least one model.
Also ensures abbenay.providers is not empty when Abbenay is enabled.
*/}}
{{- define "apme.abbenay.validateProviderSchema" -}}
{{- if not .Values.abbenay.providers }}
{{- fail `

abbenay.providers is empty. At least one provider must be configured
for Abbenay to function. Example:

  abbenay:
    providers:
      openrouter:
        engine: openrouter
        apiKeySecret: { name: my-secret, key: api-key }
        models:
          anthropic/claude-sonnet-4-6: {}

See docs/guides/ABBENAY_AI.md for all supported engines.
` }}
{{- end }}
{{- range $name, $provider := .Values.abbenay.providers }}
  {{- if not $provider.engine }}
  {{- fail (printf "abbenay.providers.%s.engine is required (one of: openrouter, anthropic, vertex-anthropic, ollama)" $name) }}
  {{- end }}
  {{- if not $provider.models }}
  {{- fail (printf "abbenay.providers.%s.models is required (map of model IDs, e.g. {claude-sonnet-4-6: {}})" $name) }}
  {{- end }}
{{- end }}
{{- end -}}

{{/*
Validate that API-key-based providers have authentication configured.
Only engines known to require an explicit API key are validated here.
Engines with ambient/keyless auth (vertex-anthropic uses GCP ADC, ollama
and lmstudio are local, bedrock uses AWS IAM, mock is for testing) are
exempt.
*/}}
{{- define "apme.abbenay.validateProviderAuth" -}}
{{- $keyRequired := list "openrouter" "anthropic" "openai" "azure-openai" "mistral" "groq" "together" "anyscale" "deepinfra" "fireworks" "perplexity" "cohere" }}
{{- range $name, $provider := .Values.abbenay.providers }}
  {{- if has $provider.engine $keyRequired }}
    {{- if and (not $provider.apiKey) (not $provider.apiKeySecret) }}
    {{- fail (printf "abbenay.providers.%s requires either apiKey or apiKeySecret for engine %q" $name $provider.engine) }}
    {{- end }}
  {{- end }}
{{- end }}
{{- end -}}

{{/*
Validate Vertex AI ADC providers have required GCP configuration.
A vertex-anthropic provider using ADC (no baseUrl, no apiKey/apiKeySecret)
requires gcp.project so the chart can set GOOGLE_VERTEX_PROJECT.
Credentials are NOT required at chart time — workload identity, attached
service accounts, and GCE metadata server provide ambient ADC without
Secrets.
*/}}
{{- define "apme.abbenay.validateVertexADC" -}}
{{- $needsADC := false -}}
{{- range $name, $provider := .Values.abbenay.providers }}
  {{- if and (eq $provider.engine "vertex-anthropic") (not $provider.baseUrl) (not $provider.apiKey) (not $provider.apiKeySecret) }}
    {{- $needsADC = true -}}
  {{- end }}
{{- end }}
{{- if $needsADC }}
  {{- if not .Values.abbenay.gcp.project }}
  {{- fail `

abbenay.gcp.project is required when using vertex-anthropic providers with ADC.

Without it, the GOOGLE_VERTEX_PROJECT env var is empty and Vertex AI API
calls will fail with 'undefined' in the URL.

  abbenay:
    gcp:
      project: "your-gcp-project-id"
      location: us-east5
      # Credentials are optional when using workload identity / attached SA.
      # Set one of these only if NOT using ambient ADC:
      #   existingSecret: apme-gcp-credentials
      #   serviceAccountKey: "<json>"
` }}
  {{- end }}
  {{- if not .Values.abbenay.gcp.location }}
  {{- fail `

abbenay.gcp.location is required when using vertex-anthropic providers with ADC.

Without it, the GOOGLE_VERTEX_LOCATION env var is empty and Vertex AI API
URLs resolve to an invalid endpoint.

  abbenay:
    gcp:
      project: "your-gcp-project-id"
      location: us-east5          # <-- required: your Vertex AI region
` }}
  {{- end }}
{{- end }}
{{- end -}}

{{/*
Reject non-loopback gRPC binds without TLS or explicit --insecure (Abbenay C2 policy).
*/}}
{{- define "apme.abbenay.validateGrpcBind" -}}
{{- if .Values.abbenay.enabled }}
  {{- $host := .Values.abbenay.grpc.host | trim -}}
  {{- $loopback := list "127.0.0.1" "localhost" "::1" -}}
  {{- $isLoopback := false -}}
  {{- range $lb := $loopback }}
    {{- if eq $host $lb }}
      {{- $isLoopback = true }}
    {{- end }}
  {{- end }}
  {{- if and (not $isLoopback) (not .Values.abbenay.grpc.tls) (not .Values.abbenay.grpc.insecure) }}
  {{- fail `

abbenay.grpc.host is a non-loopback address but neither abbenay.grpc.tls nor
abbenay.grpc.insecure is enabled. Abbenay rejects daemon binds that are not
loopback unless --grpc-tls or --insecure is set (issue #400).

Use one of:
  - abbenay.grpc.tls: true (with caCertSecret for engine→Abbenay TLS), or
  - abbenay.grpc.insecure: true (plaintext; transitional only), or
  - abbenay.grpc.host: "127.0.0.1" for loopback-only binds.

See deploy/helm/apme/templates/NOTES.txt for production TLS guidance.
` }}
  {{- end }}
{{- end }}
{{- end -}}

{{/*
Require CA Secret when engine→Abbenay TLS is enabled so Primary receives a mounted PEM.
*/}}
{{- define "apme.abbenay.validateGrpcTls" -}}
{{- if and .Values.abbenay.enabled .Values.abbenay.grpc.tls }}
  {{- if not .Values.abbenay.grpc.caCertSecret.name }}
  {{- fail `

abbenay.grpc.caCertSecret.name is required when abbenay.grpc.tls=true.

Mount an existing Secret containing the Abbenay CA PEM into the engine Primary
container and set caCertPath to the mounted file path (default /etc/abbenay-tls/ca.crt):

  abbenay:
    grpc:
      tls: true
      insecure: false
      caCertPath: "/etc/abbenay-tls/ca.crt"
      caCertSecret:
        name: "my-abbenay-ca"
        key: "ca.crt"

See deploy/helm/apme/templates/NOTES.txt for production TLS guidance.
` }}
  {{- end }}
  {{- if not .Values.abbenay.grpc.caCertSecret.key }}
  {{- fail "abbenay.grpc.caCertSecret.key is required when abbenay.grpc.tls=true (the key within the Secret)" }}
  {{- end }}
{{- end }}
{{- end -}}
