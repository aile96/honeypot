Honeypot based on the Astronomy Shop of Open Telemetry:
    Documentation: https://opentelemetry.io/docs/demo/architecture/
    Git repository: https://github.com/open-telemetry/opentelemetry-demo/tree/main

Il sistema sarà diviso in zone funzionali e sarà simulato del traffico tramite un load-generator (traffico normale) ed un pod MITRE Caldera (kill chains):
    Struttura Cluster divisa in zone funzionali (namespaces per k8s):
        Application Zone: Ospita quasi tutti i microservizi necessari al funzionamento dell'applicazione: accounting, ad, cart, checkout, currency, email, fraud-detection, frontend, kafka, loginservice (servizio aggiuntivo), product-catalog, quote, recommendation, shipping.
        Data Zone: Contiene tutti i dati dei DBs (carte di credito, info utente e currency).
        DMZ: Esposta su Internet, ospita i servizi pubblici: un SMTP server vulnerabile a command injection in cui simulare l'ingresso nella rete (potrebbe essere sostituito direttamente con un server SSH), image-provider, frontend-proxy.
        Employers & Test Zone: Zona dove gli Employers fanno i test e caricano le immagini delle cose che andranno in produzione nel registry delle immagini. Si collega a Internet dalla quale i dipendenti prendono codice ed immagini ed ospita un server per uno shared fs (tipo Samba).
        Management & Monitoring Zone: Zona di controllo che si collega con tutte le zone per raccolgiere metriche e logs, implementa una dashboard (grafana, opensearch, prometheus, jaeger, otel-collector). Ospita i servizi per attivare/disattivare funzionalità (flagd, flagd-ui), per la gestione del cluster come un repository per le immagini e Flux CD per caricare automaticamente le ultime versioni delle immagini in Application Zone e un vecchio backup di DB etcd con password funzionante per flagd.
        Payment Zone: Zona critica in cui si effettuano i pagamenti e compaiono dati molto sensibili, ospita il servizio payment.
    
    Verrà usato kind con simulazione cluster multinode:
		2 nodi Employers & Test Zone - 3 nodi Application Zone e DMZ - 1 nodo Monitoring & Management - 1 nodo Payment Zone - 1 nodo Data Zone - 1 nodo ancora da integrare bene nel cluster
	
    Aggiunte e modifiche effettuate ad Astronomy Shop:
        Aggiunte:
			DBs di carte di credito, info utente e currency con i loro Secrets
			Shared filesystem (es: samba server) con Token pieno controllo di Test Zone
			Registry immagini vulnerabile a password enumeration in Monitoring & Management Zone
			Flux CD deployato che aggiorna in automatico le immagini nel cluster all'ultima versione
			loginservice in application zone (necessario per legare carte di credito ad una identita`) che espone un'API sensibile a SQL Injection per i dati utente
			NAT server (o simulazione) - nel grafico inteso come "Internet"
			SMTP server vulnerabile con troppi privilegi sull'host (o semplicemente subito un server SSH per simulare l'exploit)
			Vecchi backup database etcd in Employers & Test Zone con password al server flagd
		Modifiche:
			In currencyservice sostituito hardcoded data con DB
			frontend con l'aggiunta del login e registrazione e possibilita` di salvare dati carte in DB per utenti autenticati
			paymentservice per il pagamento deve avere comunicazione autenticata
			checkoutservice prima controlla se ci sono dati di carte di credito salvate nel DB e poi richiede al paymentservice di recuperare i dati con richiesta autenticata
			flagd e flagd-ui autenticato e inserimento di un flag che permette traffico (non) autenticato per paymentservice
			flagd-ui non raggiungibile dal proxy esterno
			Log server senza autenticazione
			image-provider nuova API per permettere SSRF (nella richiesta c'è l'URL dove prendere immagini - http://127.0.0.1:8001/api/v1/namespaces/default/pods - https://kubernetes.default.svc/api/v1/secrets)
		
	Scopi: 
		Rubare dati utente (prendere dati autenticazione DB user [loginservice])
		Rubare carte di credito (prendere dati autenticazione DB payment - MITM sul paymentservice)
		Employers & Test zone per codice di mining
		Currencyservice DB con nuova valuta con tasso di cambio per effettuare pagamenti a costo zero
		
	Kill chains:
		Per la Kill chain usare load-generator per traffico normale e MITRE Caldera (oppure Kubesploit che è più centrato su k8s) e/o dei proxy sidecar per gli attacchi
		
		Accesso iniziale in Employers & Test Zone con immagine fraudolenta: reverse shell che apre verso un C&C [Deploy Container (T1610), Command and Scripting Interpreter: Unix Shell (T1059.004)] -> password enumeration filesystem condiviso [Brute Force: Password Guessing (T1110.001), Data from Network Shared Drive (T1039)] -> Token con diritti di creazione pod in Employers & Test Zone [Unsecured Credentials: Credentials in Files (T1552.001), Steal Application Access Token (T1528)] -> Controllo Employers & Test Zone [Masquerading: Match Legitimate Name or Location (T1036.005), Resource Hijacking (T1496)]
		
		Accesso iniziale: simulazione credenziali WiFi rubate in Employers & Test Zone [Wi-Fi Networks (T1669), Valid Accounts (T1078)] -> Credenziali deboli e registry immagini vulnerabile a password enumeration [Account Discovery (T1087), Brute Force: Password Guessing (T1110.001)] -> Modifica immagine Checkoutservice con internamente un proxy invisibile (MITM) [Develop Capabilities: Malware (T1587.006), Obtain Capabilities: Exploits (T1588.006), Masquerading: Match Legitimate Name or Location (T1036.005)] -> Logga tutti i dati delle carte di credito nei server di log aperti [Input Capture (T1056), Automated Collection (T1119), Disable or Modify Tools: Disable or Reconfigure Logging (T1562.001)] -> Esfiltrazione dati da Employers Zone raccogliendoli dai logs in Monitoring & Management [Exfiltration Over C2 Channel / Network Protocol (T1041)]
		
		Accesso iniziale in DMZ* su SMTP vulnerabile a command injection: libreria vulnerabile con cui si apre un SSH e si imposta la chiave dell'attaccante [Exploit Public-Facing Application (T1190), Command and Scripting Interpreter (T1059), Account Manipulation (T1098), Remote Service: SSH (T1021.004)] -> Vecchio backup etcd non protetto in Monitoring & Management Zone con password a flagd [Credentials from Password Stores (T1555)] -> Attivazione del flag di test in prod da server flagd che rende il traffico da checkoutservice a paymentservice senza autenticazione [Exploitation for Client Execution (T1203)] -> DNS poisoning (si puo cambiare il ConfigMaps del DNS con user non autenticato al API server) che inoltra il traffico al pod controllato dall'attaccante [Impersonation (T1656)] -> Esfiltrazione dati carte di credito: enumerazione degli utenti [Data from Local System (T1005), Exfiltration Over C2 Channel (T1041)]
		
		Accesso iniziale con token del frontend rubato con path traversal con diritti di proxy [Exploit Public-Facing Application (T1190), Unsecured Credentials: Credentials in Files (T1552.001)] -> API server esposto su internet ed accesso a tutta la rete interna con proxy [Use Alternate Authentication Material: Application Access Token (T1550.001)] -> Enumerazione password currency DB [Brute Force: Password Guessing (T1110.001)] -> Creazione nuova valuta con cambio 0 [Data Manipulation (T1499)] -> Ordini a costo 0 [Resource Hijacking (T1496)]
		
		Accesso iniziale con file \$HOME/.kube/config rubato con permesso get secrets [Steal Application Access Token (T1528)] -> Furto password DBs [Container and Resource Discovery (T1613), Unsecured Credentials: Container API (T1552.007)] -> Accesso fisico alla macchina host dei DB [Hardware Additions (T1200), Data from Local System (T1005)] -> Esfiltrazione Dati con hardware inserito [Exfiltration Over Physical Medium: Exfiltration over USB (T1052.001)]
		
		Accesso iniziale in DMZ* -> Applicazione sensibile a SQL Injection (non dal frontend ma dal microservice) [Command and Scripting Interpreter: SQL (T1059.007)] -> Furto dati utenti [Exfiltration Over C2 Channel / Network Protocol (T1041)]
		
		Accesso iniziale in DMZ* -> Server SMTP e applicazione currency nello stesso nodo ma SMTP ha accesso al socket di docker ed esegue comandi espandendosi nell'altro [Escape to Host (T1611), Container Administration Command (T1609)] -> Pagamenti gratuiti [System Information Discovery (T1082), Data Manipulation (T1565)]
		
		Accesso iniziale in DMZ* -> DoS Nodo ospite del server SMTP finchè non si ritrova sullo stesso nodo di checkout service e può rubare dati carte di credito [Endpoint Denial of Service (T1499), Modify Cloud Compute Infrastructure (T1578), Escape to Host (T1611), Container Administration Command (T1609), Exfiltration Over C2 Channel (T1041)]
		
		Accesso iniziale in DMZ* -> image-provider suscettibile a SSRF verso l'API server con privilegi [Exploitation for Client Execution (T1203)] -> Ottenimento informazioni [Cloud Service Discovery (T1526), Container and Resource Discovery (T1613)] -> Possibilità di ottenere Secrets [Unsecured Credentials: Container API (T1552.007)] -> DBs rubati [Valid Accounts: Local Accounts (T1078.003), Exfiltration Over C2 Channel (T1041)]
		
		Accesso in Employers & Test Zone da ex dipendente [Valid Accounts: Local Accounts (T1078.003)] -> Accesso a diritto di modificare e creare i namespaces e creazione nuovo namespace che comunica con tutti [Modify Cloud Compute Infrastructure (T1578)] -> Creazione server SSH [Command and Scripting Interpreter (T1059), Create Account (T1136)] -> Creazione service che ha externalIP come l'IP di paymentservice [Network Sniffing (T1040), Adversary-in-the-Middle: Evil Twin (T1557.004), Exfiltration Over Network Channel (T1041)]
		
		{Un nuovo nodo è appena stato integrato nel cluster ma a causa di una malconfigurazione del proxy frontend è accessibile da internet con kubelet aperto sulla porta 10250 -> esecuzione /exec/ o /run/ -> Fa parte del ns di default con accesso ad ogni zona e si autentica come nodo (rubato dal fs del nodo) all'API server -> Creazione di mining in Test & Employers Zone e sul nodo nuovo}
		
		Fase discovery che può rientrare in ognuna di questa kill chains:
			SSRF o semplicemente anonymous-auth=true con diritti di ricevere info sul cluster [T1087.002 – Account Discovery: Domain Accounts, T1526 – Cloud Service Discovery]
			Esplorazione con token o con certificato [T1087.002 – Account Discovery: Domain Accounts, T1526 – Cloud Service Discovery]
			Esplorazione con tool come nmap (network) o LinPEAS-k8s (container) [T1046 – Network Service Scanning, T1083 – File and Directory Discovery, T1082 – System Information Discovery, T1613 – Container and Resource Discovery]
			DNS e service discovery [T1018 – Remote System Discovery, T1613 – Container and Resource Discovery]
			Enumerazione dei log [T1654 - Log Enumeration]
			Esplorazione del sistema (container, macchina virtuale, nodo) [T1082 – System Information Discovery, T1083 – File and Directory Discovery, T1613 – Container and Resource Discovery]
			Richiesta all'API server su quali diritti si posseggono [T1087.001 – Account Discovery: Local Accounts, T1526 – Cloud Service Discovery]
			Esplorazione del filesystem e del filesystem condiviso [T1083 – File and Directory Discovery, T1135 - Network Share Discovery]
			Sniffing: con hostNetwork:true o CAP_NET_RAW può dumpare traffico su cni0 o eth0 [T1040 – Network Sniffing]
			(valuta se inserire una dashboard k8s con permessi limitati solo alla visione del cluster senza possibilità di creare cose)

Per avviare il sistema (trovarsi nel path principale dove si trova questo documento): 
    - Bisogna avviare il cluster kind con:
        kind create cluster --name dev-cluster --config kind-multinode.yaml 
    - Bisogna avviare un registry di immagini ed il suo namespace che farà parte del cluster con:
        kubectl apply -f registry.yaml
    - Permettere a Kind di fare il push delle immagini create sul registry del cluster con:
        kubectl port-forward -n registry svc/registry 5000:5000
    - Avviare la build ed il deploy del sistema con:
        skaffold run

Per avere tutte le immagini necessarie alla build del progetto:
    docker pull mcr.microsoft.com/dotnet/sdk:8.0
    docker pull mcr.microsoft.com/dotnet/aspnet:8.0
    docker pull eclipse-temurin:21-jdk
    docker pull eclipse-temurin:21-jre
    docker pull mcr.microsoft.com/dotnet/sdk:8.0
    docker pull mcr.microsoft.com/dotnet/runtime-deps:8.0-alpine3.20
    docker pull golang:1.24-bookworm
    docker pull gcr.io/distroless/static-debian12:nonroot
    docker pull alpine:3.18
    docker pull ruby:3.4.4-alpine3.21
    docker pull node:22-alpine
    docker pull node:22
    docker pull gradle:8-jdk17
    docker pull gcr.io/distroless/java17-debian12:nonroot
    docker pull envoyproxy/envoy:v1.32-latest
    docker pull nginx:1.27.0-otel
    docker pull apache/kafka:3.9.1
    docker pull python:3.12-slim-bookworm
    docker pull php:8.3-cli
    docker pull composer:2.7
    docker pull python:3.12-alpine3.
    docker pull rust:1.76
    docker pull debian:bookworm-slim
	docker pull ghcr.io/open-telemetry/demo:latest-flagd-ui
	docker pull ghcr.io/open-feature/flagd:v0.11.1
	docker pull valkey/valkey:7.2-alpine
	docker pull ghcr.io/open-telemetry/demo:2.0.2-kafka