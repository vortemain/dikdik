FROM mcr.microsoft.com/playwright:v1.64.1-noble
WORKDIR /opt/dikdik-browser
LABEL org.dikdik.browser.protocol="1"
RUN npm install --ignore-scripts --no-audit --no-fund --save-exact playwright-core@1.64.1
COPY worker.cjs /opt/dikdik-browser/worker.cjs
USER 65534:65534
ENTRYPOINT []
CMD ["node", "/opt/dikdik-browser/worker.cjs"]
