{{/*
Get Pod Env
- Merges default environment variables (if used) with component environment variables.
- If using defaults, will pull out OTEL_RESOURCE_ATTRIBUTES from the list to be reused later.
- An environment variable named OTEL_RESOURCE_ATTRIBUTES_EXTRA will have its value appended to the value of the
OTEL_RESOURCE_ATTRIBUTES environment variable if it exists.
- The OTEL_RESOURCES_ATTRIBUTES environment variable will typically use Kubernetes environment variable expansion and
should be last.
*/}}
{{- define "otel-demo.pod.env" -}}
{{- $resourceAttributesEnv := dict }}
{{- $allEnvs := list }}

{{- if .useDefault.env  }}
{{-   $defaultEnvs := include "otel-demo.envOverriden" (dict "env" .defaultValues.env "envOverrides" .defaultValues.envOverrides) | mustFromJson }}
{{-   range $defaultEnvs }}
{{-     if eq .name "OTEL_RESOURCE_ATTRIBUTES" }}
{{-       $resourceAttributesEnv = . }}
{{-     else }}
{{-       $allEnvs = append $allEnvs . }}
{{-     end }}
{{-   end }}
{{- end }}

{{- if or .env .envOverrides }}
{{-   $localEnvs := include "otel-demo.envOverriden" . | mustFromJson }}
{{-   range $localEnvs }}
{{-     if eq .name "OTEL_RESOURCE_ATTRIBUTES" }}
{{-       $resourceAttributesEnv = . }}
{{-     else if and $resourceAttributesEnv (eq .name "OTEL_RESOURCE_ATTRIBUTES_EXTRA") }}
{{-       $newValue := (printf "%s,%s" (get $resourceAttributesEnv "value") .value) }}
{{-       $resourceAttributesEnv = dict "name" "OTEL_RESOURCE_ATTRIBUTES" "value" $newValue }}
{{-     else }}
{{-       $allEnvs = append $allEnvs . }}
{{-     end }}
{{-   end }}
{{- end }}

{{- range $i, $e := .envSwitchFrom }}
  {{- $refBody := dict "name" $e.refName "key" $e.key }}
  {{- if hasKey $e "optional" }}
    {{- $refBody = merge $refBody (dict "optional" $e.optional) }}
  {{- end }}
  {{- $valueFrom := dict }}
  {{- if eq $e.valueFrom "secret" }}
    {{- $valueFrom = dict "secretKeyRef" $refBody }}
  {{- else if eq $e.valueFrom "configmap" }}
    {{- $valueFrom = dict "configMapKeyRef" $refBody }}
  {{- else }}
    {{- fail (printf "envSwitchFrom[%d]: valueFrom must be 'secret' or 'configmap'" $i) }}
  {{- end }}
  {{- $allEnvs = append $allEnvs (dict "name" $e.name "valueFrom" $valueFrom) }}
{{- end }}

{{- if $resourceAttributesEnv }}
{{-   $allEnvs = append $allEnvs $resourceAttributesEnv }}
{{- end }}

{{- range $e := $allEnvs }}
- name: {{ $e.name | quote }}
  {{- if $e.valueFrom }}
  valueFrom:
    {{- toYaml $e.valueFrom | nindent 4 }}
  {{- else }}
  value: {{ printf "%v" $e.value | quote }}
  {{- end }}
{{- end }}
{{- end }}


{{/*
Get Pod ports
*/}}
{{- define "otel-demo.pod.ports" -}}
{{- if .ports }}
{{-   range $port := .ports }}
- containerPort: {{ $port.targetPort | default $port.value }}
  name: {{ $port.name}}
{{-   end }}
{{- end }}
{{- if .service }}
{{-   if .service.port }}
- containerPort: {{.service.port}}
  name: service
{{-   end }}
{{- end }}
{{- end }}
