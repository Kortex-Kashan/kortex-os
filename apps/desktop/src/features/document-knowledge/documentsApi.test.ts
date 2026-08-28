import { beforeEach, describe, expect, it, vi } from "vitest";

const { invokeCapabilityMock } = vi.hoisted(() => ({ invokeCapabilityMock: vi.fn() }));

vi.mock("@/ipc/client", () => ({
  invokeCapability: invokeCapabilityMock,
}));

import {
  DocumentAccessDeniedError,
  DocumentRequestError,
  listDocumentAdapters,
  listDocumentTemplates,
} from "./documentsApi";

beforeEach(() => {
  invokeCapabilityMock.mockReset();
});

function successEnvelope(result: unknown) {
  return {
    requestId: "req-1",
    correlationId: "corr-1",
    status: "SUCCESS" as const,
    payload: { result },
    errors: [],
    warnings: [],
    executionDurationMs: 1,
  };
}

function failureEnvelope(category: string, message: string) {
  return {
    requestId: "req-1",
    correlationId: "corr-1",
    status: "FAILURE" as const,
    payload: null,
    errors: [{ category, message, correlationId: "corr-1" }],
    warnings: [],
    executionDurationMs: 1,
  };
}

describe("listDocumentAdapters", () => {
  it("calls the kortex.document.adapter.list capability with no parameters", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    await listDocumentAdapters();

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({ capabilityName: "kortex.document.adapter.list", parameters: {} }),
    );
  });

  it("maps an empty registry to an empty array", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    expect(await listDocumentAdapters()).toEqual([]);
  });

  it("maps the raw snake_case AdapterMetadata wire shape into a typed, camelCase DocumentAdapter", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([
        {
          adapter_id: "kortex.document.dummy.v1",
          display_name: "Dummy Reference Adapter",
          vendor: "KORTEX OS",
          author: "KORTEX Core Team",
          version: "1.0.0",
          license: "MIT",
          description: "Deterministic reference adapter.",
          homepage: null,
          supported_capabilities: ["PREVIEW"],
          supported_operations: ["GENERATE"],
          supports_preview: true,
          supports_streaming: false,
          supports_macros: false,
          supports_security: false,
          supports_versioning: false,
        },
      ]),
    );

    const [adapter] = await listDocumentAdapters();

    expect(adapter).toEqual({
      adapterId: "kortex.document.dummy.v1",
      displayName: "Dummy Reference Adapter",
      vendor: "KORTEX OS",
      author: "KORTEX Core Team",
      version: "1.0.0",
      license: "MIT",
      description: "Deterministic reference adapter.",
      homepage: null,
      supportedCapabilities: ["PREVIEW"],
      supportedOperations: ["GENERATE"],
      supportsPreview: true,
      supportsStreaming: false,
      supportsMacros: false,
      supportsSecurity: false,
      supportsVersioning: false,
    });
  });

  it("throws DocumentAccessDeniedError on a PERMISSION_DENIED failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "denied"));

    await expect(listDocumentAdapters()).rejects.toBeInstanceOf(DocumentAccessDeniedError);
  });

  it("throws DocumentRequestError on any other failure category", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("SERVICE_UNAVAILABLE", "backend unreachable"));

    await expect(listDocumentAdapters()).rejects.toBeInstanceOf(DocumentRequestError);
  });
});

describe("listDocumentTemplates", () => {
  it("calls the kortex.document.template.list capability with no parameters", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(successEnvelope([]));

    await listDocumentTemplates();

    expect(invokeCapabilityMock).toHaveBeenCalledWith(
      expect.objectContaining({ capabilityName: "kortex.document.template.list", parameters: {} }),
    );
  });

  it("maps the raw snake_case TemplateSchema wire shape into a typed, camelCase DocumentTemplate", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(
      successEnvelope([
        {
          template_id: "payslip.declarative.v1",
          name: "Payslip",
          namespace: "hr",
          version: "1.0.0",
          description: "Standard payslip template.",
          placeholders: ["employee_name"],
          required_fields: ["employee_id"],
        },
      ]),
    );

    const [template] = await listDocumentTemplates();

    expect(template).toEqual({
      templateId: "payslip.declarative.v1",
      name: "Payslip",
      namespace: "hr",
      version: "1.0.0",
      description: "Standard payslip template.",
      placeholders: ["employee_name"],
      requiredFields: ["employee_id"],
    });
  });

  it("throws DocumentAccessDeniedError on a PERMISSION_DENIED failure", async () => {
    invokeCapabilityMock.mockResolvedValueOnce(failureEnvelope("PERMISSION_DENIED", "denied"));

    await expect(listDocumentTemplates()).rejects.toBeInstanceOf(DocumentAccessDeniedError);
  });
});
