export interface Config {
  ansible?: {
    apme?: {
      /** Server-only location of the Portal settings store. */
      portalSettingsPath?: string;
      /** Allow HTTP URLs for explicitly configured development SCM servers. */
      allowHttpOutboundUrls?: boolean;
      /** Allow private network SCM hosts only when deployment policy permits. */
      allowPrivateOutboundUrls?: boolean;
    };
  };
  apme?: {
    /** Host backend API prefix. Local defaults to discovery('proxy')/apme.
     * @visibility frontend
     */
    apiBase?: string;
    /**
     * Authenticated upload backend when uploads are enabled; defaults to discovery('apme').
     * @visibility frontend
     */
    uploadApiBase?: string;
    /**
     * Explicitly enable Local uploads when the authenticated backend plugin is installed. Defaults off.
     * @visibility frontend
     */
    uploads?: {
      /**
       * @visibility frontend
       */
      enabled?: boolean;
    };
  };
}
