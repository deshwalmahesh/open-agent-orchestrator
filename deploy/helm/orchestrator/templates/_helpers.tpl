{{- define "orchestrator.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "orchestrator.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "orchestrator.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "orchestrator.labels" -}}
app.kubernetes.io/name: {{ include "orchestrator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* Name of the Secret to mount — existing one if provided, else our generated one. */}}
{{- define "orchestrator.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-secret" (include "orchestrator.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/* Shared envFrom (config + secrets) for api / worker / migrate pods. */}}
{{- define "orchestrator.envFrom" -}}
- configMapRef:
    name: {{ include "orchestrator.fullname" . }}-config
- secretRef:
    name: {{ include "orchestrator.secretName" . }}
{{- end -}}
