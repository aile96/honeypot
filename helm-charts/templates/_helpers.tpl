{{/*
Expand the name of the chart.
*/}}
{{- define "otel-demo.name" -}}
{{- default .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "otel-demo.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "otel-demo.labels" -}}
helm.sh/chart: {{ include "otel-demo.chart" . }}
{{ include "otel-demo.selectorLabels" . }}
{{ include "otel-demo.workloadLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/part-of: opentelemetry-demo
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}



{{/*
Workload (Pod) labels
*/}}
{{- define "otel-demo.workloadLabels" -}}
{{- if .name }}
app.kubernetes.io/component: {{ .name}}
app.kubernetes.io/name: {{ .name }}
{{- end }}
{{- end }}




{{/*
Selector labels
*/}}
{{- define "otel-demo.selectorLabels" -}}
{{- if .name }}
opentelemetry.io/name: {{ .name }}
{{- end }}
{{- end }}

{{- define "otel-demo.envOverriden" -}}
{{- $mergedEnvs := list }}
{{- $envOverrides := default (list) .envOverrides }}

{{- range .env }}
{{-   $currentEnv := . }}
{{-   $hasOverride := false }}
{{-   range $envOverrides }}
{{-     if eq $currentEnv.name .name }}
{{-       $mergedEnvs = append $mergedEnvs . }}
{{-       $envOverrides = without $envOverrides . }}
{{-       $hasOverride = true }}
{{-     end }}
{{-   end }}
{{-   if not $hasOverride }}
{{-     $mergedEnvs = append $mergedEnvs $currentEnv }}
{{-   end }}
{{- end }}
{{- $mergedEnvs = concat $mergedEnvs $envOverrides }}
{{- mustToJson $mergedEnvs }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "otel-demo.serviceAccountName" -}}
{{- if .serviceAccount.create }}
{{- default (include "otel-demo.name" .) .serviceAccount.name }}
{{- else }}
{{- default "default" .serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "np.renderFor" -}}
{{- $ns := .ns -}}
{{- $policy := .policy -}}
{{- $root := .root -}}
{{- $dns := (default dict $root.Values.networkPolicies.dns) -}}
{{- $allowSelf := (default true $root.Values.networkPolicies.allowSelfNamespace) -}}
{{- $internet := (default dict $root.Values.networkPolicies.internet) -}}
{{- $enabledNs := (default (list) $internet.enabledNamespaces) -}}

apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ $ns }}-base
  namespace: {{ $ns | quote }}
spec:
  podSelector: {}
  policyTypes: ["Ingress","Egress"]

  ingress:
    {{- if $allowSelf }}
    - from:
        - podSelector: {}
    {{- end }}
    {{- if $policy.ingress }}
    - from:
    {{- range $from := $policy.ingress }}
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ $from | quote }}
    {{- end }}
    {{- end }}

  egress:
    # 1) DNS verso kube-system (CoreDNS)
    {{- if and $dns.enabled $dns.namespace }}
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ $dns.namespace | quote }}
          {{- if $dns.podSelector }}
          podSelector:
            matchLabels:
            {{- range $k, $v := $dns.podSelector }}
              {{ $k }}: {{ $v | quote }}
            {{- end }}
          {{- end }}
      ports:
      {{- $ports := (default (list 53) $dns.ports) -}}
      {{- range $p := $ports }}
        - protocol: UDP
          port: {{ $p }}
        - protocol: TCP
          port: {{ $p }}
      {{- end }}
    {{- end }}

    # 2) intra-namespace
    {{- if $allowSelf }}
    - to:
        - podSelector: {}
    {{- end }}

    # 3) egress verso namespace consentiti
    {{- if $policy.egress }}
    - to:
    {{- range $to := $policy.egress }}
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ $to | quote }}
    {{- end }}
    {{- end }}

    # 4) Internet (solo ns abilitati)
    {{- if has $ns $enabledNs }}
    - to:
        - ipBlock:
            cidr: {{ default "0.0.0.0/0" $internet.allowCIDR | quote }}
            {{- $except := (default (list) $internet.exceptCIDRs) }}
            {{- if $except }}
            except:
            {{- range $cidr := $except }}
              - {{ $cidr | quote }}
            {{- end }}
            {{- end }}
      {{- if $internet.ports }}
      ports:
      {{- range $p := $internet.ports }}
        - protocol: TCP
          port: {{ $p }}
      {{- end }}
      {{- end }}
    {{- end }}

---
{{- end -}}