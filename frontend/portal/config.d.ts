/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

export interface Config {
  ansible?: {
    /**
     * @deepVisibility frontend
     */
    devSpaces?: {
      /**
       * OpenShift Dev Spaces dashboard base URL.
       * @visibility frontend
       */
      baseUrl?: string;
    };
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
}
