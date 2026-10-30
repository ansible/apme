export interface Config {
  apme?: {
    /** Host backend API prefix. Local defaults to discovery('proxy')/apme.
     * @visibility frontend
     */
    apiBase?: string;
    /** Authenticated upload backend; defaults to discovery('apme'). @visibility frontend */
    uploadApiBase?: string;
  };
}
