FROM nginx:1.27-alpine

# Remove default config
RUN rm /etc/nginx/conf.d/default.conf

# Custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy frontend files
COPY index.html /usr/share/nginx/html/index.html

# Cloud Run expõe porta 8080
EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
