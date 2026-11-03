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

import {
  getApmeConfig,
  isApmeEnabled,
  isApmePublishViaGateway,
} from "./config";

function mockApmeSection(
  overrides: {
    getOptionalBoolean?: jest.Mock;
    getString?: jest.Mock;
    getOptionalString?: jest.Mock;
    getOptionalNumber?: jest.Mock;
  } = {},
) {
  return {
    getOptionalBoolean: jest.fn().mockReturnValue(undefined),
    getString: jest.fn().mockReturnValue("http://localhost:8080"),
    getOptionalString: jest.fn().mockReturnValue(undefined),
    getOptionalNumber: jest.fn().mockReturnValue(undefined),
    ...overrides,
  };
}

describe("getApmeConfig", () => {
  it("returns disabled defaults when no apme config exists", () => {
    const mockConfig = {
      getOptionalConfig: jest.fn().mockReturnValue(undefined),
    };

    expect(getApmeConfig(mockConfig as any)).toEqual({
      enabled: false,
      baseUrl: "http://localhost:8080",
      checkSSL: false,
      publishViaGateway: false,
      submitTimeoutMs: 300_000,
      targetAnsibleCoreVersion: "2.16",
      portalSettingsPath: undefined,
    });
  });

  it("returns config from ansible.apme section with trailing slash trimmed", () => {
    const mockApmeConfig = mockApmeSection({
      getOptionalBoolean: jest
        .fn()
        .mockImplementation((key: string) =>
          key === "enabled" ? true : undefined,
        ),
      getString: jest.fn().mockReturnValue("https://apme.example.com/"),
    });

    const mockConfig = {
      getOptionalConfig: jest
        .fn()
        .mockImplementation((key: string) =>
          key === "ansible.apme" ? mockApmeConfig : undefined,
        ),
    };

    expect(getApmeConfig(mockConfig as any)).toEqual({
      enabled: true,
      baseUrl: "https://apme.example.com",
      checkSSL: true,
      publishViaGateway: true,
      submitTimeoutMs: 300_000,
      targetAnsibleCoreVersion: "2.16",
      portalSettingsPath: undefined,
    });
  });

  it("defaults checkSSL to true when not specified", () => {
    const mockApmeConfig = mockApmeSection({
      getString: jest.fn().mockReturnValue("https://apme.example.com"),
    });

    const mockConfig = {
      getOptionalConfig: jest
        .fn()
        .mockImplementation((key: string) =>
          key === "ansible.apme" ? mockApmeConfig : undefined,
        ),
    };

    expect(getApmeConfig(mockConfig as any).checkSSL).toBe(true);
  });

  it("defaults publishViaGateway to true when omitted", () => {
    const mockApmeConfig = mockApmeSection({
      getOptionalBoolean: jest
        .fn()
        .mockImplementation((key: string) =>
          key === "enabled" ? true : undefined,
        ),
    });

    const mockConfig = {
      getOptionalConfig: jest
        .fn()
        .mockImplementation((key: string) =>
          key === "ansible.apme" ? mockApmeConfig : undefined,
        ),
    };

    expect(getApmeConfig(mockConfig as any).publishViaGateway).toBe(true);
    expect(isApmePublishViaGateway(mockConfig as any)).toBe(true);
  });

  it("honors explicit publishViaGateway false", () => {
    const mockApmeConfig = mockApmeSection({
      getOptionalBoolean: jest
        .fn()
        .mockImplementation((key: string) =>
          key === "publishViaGateway" ? false : undefined,
        ),
    });

    const mockConfig = {
      getOptionalConfig: jest
        .fn()
        .mockImplementation((key: string) =>
          key === "ansible.apme" ? mockApmeConfig : undefined,
        ),
    };

    expect(getApmeConfig(mockConfig as any).publishViaGateway).toBe(false);
  });

  it("defaults submitTimeoutMs to 5 minutes when omitted", () => {
    const mockApmeConfig = mockApmeSection();
    const mockConfig = {
      getOptionalConfig: jest
        .fn()
        .mockImplementation((key: string) =>
          key === "ansible.apme" ? mockApmeConfig : undefined,
        ),
    };

    expect(getApmeConfig(mockConfig as any).submitTimeoutMs).toBe(300_000);
  });

  it("honors explicit submitTimeoutMs", () => {
    const mockApmeConfig = mockApmeSection({
      getOptionalNumber: jest
        .fn()
        .mockImplementation((key: string) =>
          key === "submitTimeoutMs" ? 120_000 : undefined,
        ),
    });
    const mockConfig = {
      getOptionalConfig: jest
        .fn()
        .mockImplementation((key: string) =>
          key === "ansible.apme" ? mockApmeConfig : undefined,
        ),
    };

    expect(getApmeConfig(mockConfig as any).submitTimeoutMs).toBe(120_000);
  });

  it("rejects non-positive submitTimeoutMs and falls back to default", () => {
    const mockApmeConfig = mockApmeSection({
      getOptionalNumber: jest.fn().mockReturnValue(0),
    });
    const mockConfig = {
      getOptionalConfig: jest
        .fn()
        .mockImplementation((key: string) =>
          key === "ansible.apme" ? mockApmeConfig : undefined,
        ),
    };

    expect(getApmeConfig(mockConfig as any).submitTimeoutMs).toBe(300_000);
  });
});

describe("isApmeEnabled", () => {
  it("returns true only when enabled flag is set", () => {
    const mockConfig = {
      getOptionalBoolean: jest
        .fn()
        .mockImplementation((key: string) =>
          key === "ansible.apme.enabled" ? true : undefined,
        ),
    };

    expect(isApmeEnabled(mockConfig as any)).toBe(true);
  });
});
