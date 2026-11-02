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

import { ConfigReader } from "@backstage/config";
import {
  buildPortalPahGalaxyServers,
  isPortalManagedGalaxyServerName,
  normalizePahRepoIdentifier,
  PORTAL_HUB_GALAXY_SERVER_PREFIX,
  syncPortalGalaxyServers,
} from "./portalGalaxyServers";

describe("portalGalaxyServers", () => {
  describe("normalizePahRepoIdentifier", () => {
    it("normalizes repo names", () => {
      expect(normalizePahRepoIdentifier("rh-certified")).toBe("rh_certified");
      expect(normalizePahRepoIdentifier("__Published__")).toBe("published");
    });
  });

  describe("isPortalManagedGalaxyServerName", () => {
    it("detects portal_hub_ prefix", () => {
      expect(isPortalManagedGalaxyServerName("portal_hub_rh_certified")).toBe(
        true,
      );
      expect(isPortalManagedGalaxyServerName("galaxy")).toBe(false);
    });
  });

  describe("buildPortalPahGalaxyServers", () => {
    it("returns empty when no AAP base URL", () => {
      const config = new ConfigReader({
        catalog: {
          providers: {
            rhaap: {
              production: {
                sync: {
                  pahCollections: {
                    enabled: true,
                    repositories: [{ name: "published" }],
                  },
                },
              },
            },
          },
        },
      });
      expect(buildPortalPahGalaxyServers(config)).toEqual([]);
    });

    it("builds portal_hub servers from pahCollections", () => {
      const config = new ConfigReader({
        ansible: {
          rhaap: {
            baseUrl: "https://aap.example.com/",
            token: "secret-token",
          },
        },
        catalog: {
          providers: {
            rhaap: {
              production: {
                sync: {
                  pahCollections: {
                    enabled: true,
                    repositories: [
                      { name: "rh-certified" },
                      { name: "validated" },
                      { name: "published" },
                    ],
                  },
                },
              },
            },
          },
        },
      });

      const servers = buildPortalPahGalaxyServers(config);
      expect(servers).toHaveLength(3);
      expect(servers[0]).toEqual({
        name: `${PORTAL_HUB_GALAXY_SERVER_PREFIX}rh_certified`,
        url: "https://aap.example.com/api/galaxy/content/rh-certified/",
        token: "secret-token",
      });
      expect(servers.map((s) => s.name)).toEqual([
        "portal_hub_rh_certified",
        "portal_hub_validated",
        "portal_hub_published",
      ]);
    });

    it("skips environments with pahCollections.enabled false", () => {
      const config = new ConfigReader({
        ansible: {
          rhaap: { baseUrl: "https://aap.example.com", token: "t" },
        },
        catalog: {
          providers: {
            rhaap: {
              production: {
                sync: {
                  pahCollections: {
                    enabled: false,
                    repositories: [{ name: "published" }],
                  },
                },
              },
            },
          },
        },
      });
      expect(buildPortalPahGalaxyServers(config)).toEqual([]);
    });

    it("dedupes repository names across envs", () => {
      const config = new ConfigReader({
        ansible: {
          rhaap: { baseUrl: "https://aap.example.com", token: "t" },
        },
        catalog: {
          providers: {
            rhaap: {
              a: {
                sync: {
                  pahCollections: {
                    repositories: [{ name: "published" }],
                  },
                },
              },
              b: {
                sync: {
                  pahCollections: {
                    repositories: [{ name: "published" }],
                  },
                },
              },
            },
          },
        },
      });
      expect(buildPortalPahGalaxyServers(config)).toHaveLength(1);
    });

    it("skips PAH repo names with path metacharacters", () => {
      const config = new ConfigReader({
        ansible: {
          rhaap: { baseUrl: "https://aap.example.com", token: "t" },
        },
        catalog: {
          providers: {
            rhaap: {
              production: {
                sync: {
                  pahCollections: {
                    repositories: [
                      { name: "../evil" },
                      { name: "published with spaces" },
                      { name: "published" },
                    ],
                  },
                },
              },
            },
          },
        },
      });
      const servers = buildPortalPahGalaxyServers(config);
      expect(servers).toHaveLength(1);
      expect(servers[0].name).toBe("portal_hub_published");
      expect(servers[0].url).toBe(
        "https://aap.example.com/api/galaxy/content/published/",
      );
    });
  });

  describe("syncPortalGalaxyServers", () => {
    const emptyServer = (
      id: number,
      name: string,
      url: string,
      has_token = false,
    ) => ({
      id,
      name,
      url,
      auth_url: "",
      has_token,
      created_at: "",
      updated_at: "",
    });

    it("creates missing portal servers and skips manual ones", async () => {
      const listGalaxyServers = jest
        .fn()
        .mockResolvedValue([
          emptyServer(9, "galaxy", "https://galaxy.ansible.com/api/"),
        ]);
      const createGalaxyServer = jest.fn().mockResolvedValue({});
      const updateGalaxyServer = jest.fn().mockResolvedValue({});
      const deleteGalaxyServer = jest.fn().mockResolvedValue(undefined);

      const result = await syncPortalGalaxyServers(
        {
          listGalaxyServers,
          createGalaxyServer,
          updateGalaxyServer,
          deleteGalaxyServer,
        },
        [
          {
            name: "portal_hub_published",
            url: "https://aap.example.com/api/galaxy/content/published/",
            token: "tok",
          },
        ],
      );

      expect(result.created).toBe(1);
      expect(result.deleted).toBe(0);
      expect(createGalaxyServer).toHaveBeenCalledWith({
        name: "portal_hub_published",
        url: "https://aap.example.com/api/galaxy/content/published/",
        token: "tok",
      });
      expect(updateGalaxyServer).not.toHaveBeenCalled();
      expect(deleteGalaxyServer).not.toHaveBeenCalled();
    });

    it("updates URL when changed", async () => {
      const listGalaxyServers = jest
        .fn()
        .mockResolvedValue([
          emptyServer(
            3,
            "portal_hub_published",
            "https://old.example.com/api/galaxy/content/published/",
            true,
          ),
        ]);
      const createGalaxyServer = jest.fn();
      const updateGalaxyServer = jest.fn().mockResolvedValue({});
      const deleteGalaxyServer = jest.fn().mockResolvedValue(undefined);

      const result = await syncPortalGalaxyServers(
        {
          listGalaxyServers,
          createGalaxyServer,
          updateGalaxyServer,
          deleteGalaxyServer,
        },
        [
          {
            name: "portal_hub_published",
            url: "https://aap.example.com/api/galaxy/content/published/",
            token: "tok",
          },
        ],
      );

      expect(result.updated).toBe(1);
      expect(result.deleted).toBe(0);
      expect(updateGalaxyServer).toHaveBeenCalledWith(3, {
        url: "https://aap.example.com/api/galaxy/content/published/",
        token: "tok",
      });
      expect(createGalaxyServer).not.toHaveBeenCalled();
      expect(deleteGalaxyServer).not.toHaveBeenCalled();
    });

    it("prunes obsolete portal_hub servers and leaves manual ones", async () => {
      const listGalaxyServers = jest
        .fn()
        .mockResolvedValue([
          emptyServer(1, "galaxy", "https://galaxy.ansible.com/api/"),
          emptyServer(
            2,
            "portal_hub_validated",
            "https://aap.example.com/api/galaxy/content/validated/",
            true,
          ),
          emptyServer(
            3,
            "portal_hub_published",
            "https://aap.example.com/api/galaxy/content/published/",
            true,
          ),
        ]);
      const createGalaxyServer = jest.fn();
      const updateGalaxyServer = jest.fn().mockResolvedValue({});
      const deleteGalaxyServer = jest.fn().mockResolvedValue(undefined);

      const result = await syncPortalGalaxyServers(
        {
          listGalaxyServers,
          createGalaxyServer,
          updateGalaxyServer,
          deleteGalaxyServer,
        },
        [
          {
            name: "portal_hub_published",
            url: "https://aap.example.com/api/galaxy/content/published/",
            token: "tok",
          },
        ],
      );

      expect(result.deleted).toBe(1);
      expect(deleteGalaxyServer).toHaveBeenCalledWith(2);
      expect(deleteGalaxyServer).not.toHaveBeenCalledWith(1);
      expect(deleteGalaxyServer).not.toHaveBeenCalledWith(3);
    });

    it("prunes all portal_hub servers when desired is empty", async () => {
      const listGalaxyServers = jest
        .fn()
        .mockResolvedValue([
          emptyServer(1, "galaxy", "https://galaxy.ansible.com/api/"),
          emptyServer(
            2,
            "portal_hub_published",
            "https://aap.example.com/api/galaxy/content/published/",
            true,
          ),
        ]);
      const createGalaxyServer = jest.fn();
      const updateGalaxyServer = jest.fn();
      const deleteGalaxyServer = jest.fn().mockResolvedValue(undefined);

      const result = await syncPortalGalaxyServers(
        {
          listGalaxyServers,
          createGalaxyServer,
          updateGalaxyServer,
          deleteGalaxyServer,
        },
        [],
      );

      expect(result.desired).toBe(0);
      expect(result.deleted).toBe(1);
      expect(deleteGalaxyServer).toHaveBeenCalledWith(2);
      expect(createGalaxyServer).not.toHaveBeenCalled();
      expect(updateGalaxyServer).not.toHaveBeenCalled();
    });
  });
});
