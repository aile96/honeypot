# Dockerfile
FROM nginx:1.30.0-alpine@sha256:f60d139a69209d4340f6621fc6a50c9843702214231522a3390432f8db0ed870

# This file contains environment variables such as ${FRONTEND_PROXY}
# and ${CALDERA_SERVER}.
# The official nginx image runs envsubst on templates placed in
# /etc/nginx/templates/*.template at container startup.
COPY nginx.conf.template /etc/nginx/templates/nginx.conf.template

# Generate /etc/nginx/nginx.conf instead of /etc/nginx/conf.d/nginx.conf
ENV NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx

EXPOSE 8080 8888

CMD ["nginx", "-g", "daemon off;"]
