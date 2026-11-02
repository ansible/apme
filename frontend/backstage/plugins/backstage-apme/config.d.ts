export interface Config {
  ansible?: {
    apme?: {
      /**
       * When false, the plugin registers no routes, sidebar, or UI surfaces.
       * @visibility frontend
       */
      enabled?: boolean;
      /** Server-only location of the Portal settings store. */
      portalSettingsPath?: string;
      /** Allow HTTP URLs for explicitly configured development SCM servers. */
      allowHttpOutboundUrls?: boolean;
      /** Allow private network SCM hosts only when deployment policy permits. */
      allowPrivateOutboundUrls?: boolean;
      /**
       * APME service base URL (e.g. http://localhost:8080).
       * Quality settings can override this at runtime (portal settings store).
       * @visibility frontend
       */
      baseUrl?: string;
      /** @visibility frontend */
      checkSSL?: boolean;
      /**
       * Use mock data instead of the real APME service.
       * @visibility frontend
       */
      mockMode?: boolean;
      /**
       * When true, PR creation is proxied to the APME service SCM submit path (ADR-050).
       * @default true
       * @visibility frontend
       */
      publishViaGateway?: boolean;
      /**
       * Timeout in milliseconds for remediation submit / push branch / create PR.
       * Large remedia pushes may need longer than the default short HTTP timeout.
       * @default 300000
       * @visibility frontend
       */
      submitTimeoutMs?: number;
      /**
       * Default ansible-core scan target when the portal settings store has no
       * global value. Editable in Git Repositories → Quality settings
       * (`PUT /apme/settings`); this config is the fallback only.
       * @visibility frontend
       */
      targetAnsibleCoreVersion?: string;
      /**
       * Early-access feedback form URL. Omit for default EAP form; set "" to hide link.
       * @default https://forms.gle/VUvfyTjnVax83GPt6
       * @visibility frontend
       */
      feedbackFormUrl?: string;
    };
  };
  apme?: {
    /** Host backend API prefix. Local defaults to discovery('proxy')/apme.
     * @visibility frontend
     */
    apiBase?: string;
    /** Authenticated upload backend; defaults to discovery('apme'). @visibility frontend */
    uploadApiBase?: string;
  };
}
