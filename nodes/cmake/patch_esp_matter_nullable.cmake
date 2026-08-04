set(_nullable_header
    "${CMAKE_SOURCE_DIR}/managed_components/espressif__esp_matter/connectedhomeip/connectedhomeip/src/app/data-model/Nullable.h"
)

if(EXISTS "${_nullable_header}")
    file(READ "${_nullable_header}" _nullable_text)

    set(_nullable_old [=[
    inline bool operator==(const T & other) const { return static_cast<const std::optional<T> &>(*this) == other; }
    inline bool operator!=(const T & other) const { return !(*this == other); }

    inline bool operator==(const Nullable<T> & other) const
    {
        return static_cast<const std::optional<T> &>(*this) == static_cast<const std::optional<T> &>(other);
    }
]=])

    set(_nullable_new [=[
    inline bool operator==(const T & other) const { return !IsNull() && Value() == other; }
    inline bool operator!=(const T & other) const { return !(*this == other); }

    inline bool operator==(const Nullable<T> & other) const
    {
        if (IsNull() != other.IsNull())
        {
            return false;
        }
        return IsNull() || Value() == other.Value();
    }
]=])

    string(REPLACE "${_nullable_old}" "${_nullable_new}" _nullable_patched "${_nullable_text}")
    if(NOT _nullable_text STREQUAL _nullable_patched)
        file(WRITE "${_nullable_header}" "${_nullable_patched}")
        message(STATUS "Patched esp_matter Nullable.h for GCC 14 optional comparison")
    endif()
endif()

unset(_nullable_header)
unset(_nullable_text)
unset(_nullable_old)
unset(_nullable_new)
unset(_nullable_patched)
