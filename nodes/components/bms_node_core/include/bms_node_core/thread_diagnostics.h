#pragma once

#include "esp_err.h"
#include "esp_log.h"
#include "esp_matter.h"
#include "esp_matter_attribute_utils.h"

#include <clusters/ThreadNetworkDiagnostics/AttributeIds.h>
#include <clusters/ThreadNetworkDiagnostics/ClusterId.h>

namespace bms_node_core::thread_diagnostics {

inline esp_err_t ensure_root_identity_attributes(const char *tag)
{
    namespace thread_diag = chip::app::Clusters::ThreadNetworkDiagnostics;

    esp_matter::cluster_t *cluster = esp_matter::cluster::get(static_cast<uint16_t>(0), thread_diag::Id);
    if (!cluster) {
        ESP_LOGW(tag, "ThreadNetworkDiagnostics cluster is not present on root endpoint");
        return ESP_ERR_NOT_FOUND;
    }

    if (!esp_matter::attribute::get(cluster, thread_diag::Attributes::ExtAddress::Id)) {
        esp_matter_attr_val_t value = esp_matter_nullable_uint64(nullable<uint64_t>());
        esp_matter::attribute_t *attribute = esp_matter::attribute::create(
            cluster,
            thread_diag::Attributes::ExtAddress::Id,
            esp_matter::ATTRIBUTE_FLAG_MANAGED_INTERNALLY | esp_matter::ATTRIBUTE_FLAG_NULLABLE,
            value);
        if (!attribute) {
            ESP_LOGE(tag, "Failed to create ThreadNetworkDiagnostics ExtAddress attribute");
            return ESP_FAIL;
        }
    }

    if (!esp_matter::attribute::get(cluster, thread_diag::Attributes::Rloc16::Id)) {
        esp_matter_attr_val_t value = esp_matter_nullable_uint16(nullable<uint16_t>());
        esp_matter::attribute_t *attribute = esp_matter::attribute::create(
            cluster,
            thread_diag::Attributes::Rloc16::Id,
            esp_matter::ATTRIBUTE_FLAG_MANAGED_INTERNALLY | esp_matter::ATTRIBUTE_FLAG_NULLABLE,
            value);
        if (!attribute) {
            ESP_LOGE(tag, "Failed to create ThreadNetworkDiagnostics Rloc16 attribute");
            return ESP_FAIL;
        }
    }

    ESP_LOGI(tag, "ThreadNetworkDiagnostics ExtAddress/Rloc16 attributes enabled");
    return ESP_OK;
}

}  // namespace bms_node_core::thread_diagnostics
