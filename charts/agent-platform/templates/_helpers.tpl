{{/*
Chart name.
*/}}
{{- define "agent-platform.name" -}}
{{- .Chart.Name -}}
{{- end -}}

{{/*
Release-qualified name for a given component, e.g. "myrelease-dispatcher".
*/}}
{{- define "agent-platform.componentName" -}}
{{- printf "%s-%s" .context.Release.Name .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels.
*/}}
{{- define "agent-platform.labels" -}}
app.kubernetes.io/name: {{ include "agent-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Selector labels for a given component.
*/}}
{{- define "agent-platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agent-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Postgres connection env vars: a POSTGRES_PASSWORD sourced from the
bitnami/postgresql subchart's generated Secret, followed by AP_DB_URL which
references it via $(POSTGRES_PASSWORD) k8s env substitution.
*/}}
{{- define "agent-platform.postgresEnv" -}}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Release.Name }}-postgresql
      key: postgres-password
- name: AP_DB_URL
  value: "postgresql+asyncpg://postgres:$(POSTGRES_PASSWORD)@{{ .Release.Name }}-postgresql:5432/{{ .Values.postgresql.auth.database }}"
{{- end -}}

{{/*
Env vars shared by the api/dispatcher/recorder backend Deployments.
*/}}
{{- define "agent-platform.backendEnv" -}}
{{ include "agent-platform.postgresEnv" . }}
- name: AP_KAFKA_BOOTSTRAP
  value: "{{ .Release.Name }}-kafka:9092"
- name: AP_K8S_NAMESPACE
  value: {{ .Values.env.AP_K8S_NAMESPACE | default .Release.Namespace | quote }}
- name: AP_RUNNER_IMAGE
  value: "{{ .Values.images.runner.repository }}:{{ .Values.images.runner.tag }}"
- name: AP_AGENTS_ROOT
  value: "/agents/agents"
- name: AP_SKILLS_ROOT
  value: "/agents/skills"
- name: AP_AGENTS_VOLUME_CLAIM
  value: {{ .Values.env.AP_AGENTS_VOLUME_CLAIM | quote }}
- name: AP_GLOBAL_CONCURRENCY
  value: {{ .Values.env.AP_GLOBAL_CONCURRENCY | quote }}
- name: AP_RUN_TIMEOUT_SECONDS
  value: {{ .Values.env.AP_RUN_TIMEOUT_SECONDS | quote }}
- name: AP_GIT_REMOTE_URL
  value: {{ .Values.env.AP_GIT_REMOTE_URL | default "" | quote }}
- name: AP_GITHUB_REPO
  value: {{ .Values.env.AP_GITHUB_REPO | default "" | quote }}
- name: AP_DEFAULT_BRANCH
  value: {{ .Values.env.AP_DEFAULT_BRANCH | default "main" | quote }}
{{- end -}}
