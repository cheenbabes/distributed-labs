FROM nginx:1.25-alpine

# Install inotify-tools for config watching
RUN apk add --no-cache inotify-tools

# Copy configuration
COPY nginx.conf /etc/nginx/nginx.conf
COPY upstream.conf /etc/nginx/conf.d/upstream.conf

# Copy and set up the watcher script
COPY watch-reload.sh /watch-reload.sh
RUN chmod +x /watch-reload.sh

# Start nginx and the watcher
CMD ["/bin/sh", "-c", "/watch-reload.sh & nginx -g 'daemon off;'"]
