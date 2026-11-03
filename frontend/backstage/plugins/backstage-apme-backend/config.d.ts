export interface Config {
  apme?: {
    /** Private Gateway origin used only by the authenticated upload backend. */
    gatewayBaseUrl: string;
  };
}
