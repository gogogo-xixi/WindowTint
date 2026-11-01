#define WIN32_LEAN_AND_MEAN
#define NOMINMAX

#include <windows.h>
#include <dwmapi.h>
#include <objidl.h>
#include <gdiplus.h>
#include <process.h>

#include <cwchar>
#include <memory>
#include <mutex>
#include <new>
#include <string>
#include <unordered_map>
#include <vector>

#pragma comment(lib, "dwmapi.lib")
#pragma comment(lib, "gdiplus.lib")

extern "C" __declspec(dllexport) BOOL WINAPI Apply(HWND hwnd, LPCWSTR path);
extern "C" __declspec(dllexport) BOOL WINAPI Remove(HWND hwnd);

namespace {

constexpr size_t kMaxLongPath = 32768;
constexpr UINT kApplyOverlayMessage = WM_APP + 0x52;
constexpr UINT kRemoveMessage = WM_APP + 0x53;
constexpr UINT_PTR kOverlayTimerId = 0x57544F4C;
constexpr UINT kOverlayTimerInterval = 100;
constexpr UINT_PTR kResizeSettleTimerId = 0x57545253;
constexpr UINT kResizeSettleDelay = 120;
constexpr wchar_t kOverlayClassPrefix[] = L"WindowTintOverlay";
constexpr wchar_t kOverlayClassName[] = L"WindowTintOverlay_0_4_11";
constexpr wchar_t kStatePropertyName[] = L"WindowTintState_0_4_11";
constexpr DWORD kDwmExtendedFrameBounds = 9;

LRESULT CALLBACK OverlayWindowProc(
    HWND hwnd,
    UINT message,
    WPARAM w_param,
    LPARAM l_param);
LRESULT CALLBACK WindowHookProc(
    HWND hwnd,
    UINT message,
    WPARAM w_param,
    LPARAM l_param);

struct OverlayState {
    HWND target = nullptr;
    HWND overlay = nullptr;
    std::wstring path;
    std::unique_ptr<Gdiplus::Image> image;
    std::mutex image_mutex;
    WNDPROC original_proc = nullptr;
    bool showing = false;
    bool image_dirty = true;
    bool resize_pending = false;
    bool interactive_resize = false;
    int x = 0;
    int y = 0;
    int width = 0;
    int height = 0;
};

struct HookBinding {
    std::shared_ptr<OverlayState> state;
    bool destroying = false;
};

bool ShowOverlay(
    HWND target,
    const std::shared_ptr<OverlayState>& state,
    bool force_upload);

std::mutex g_state_mutex;
std::unordered_map<HWND, std::shared_ptr<OverlayState>> g_states;
HMODULE g_overlay_module = nullptr;

std::mutex g_gdiplus_mutex;
ULONG_PTR g_gdiplus_token = 0;
bool g_gdiplus_ready = false;
HANDLE g_probe_mapping = nullptr;
volatile LONG* g_probe_view = nullptr;

struct OwnerWatch {
    HANDLE process;
    DWORD pid;
    unsigned long generation;
};

std::mutex g_owner_mutex;
HANDLE g_owner_process = nullptr;
DWORD g_owner_pid = 0;
unsigned long g_owner_generation = 0;

HMODULE CurrentModule() {
    HMODULE module = nullptr;
    GetModuleHandleExW(
        GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
            GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        reinterpret_cast<LPCWSTR>(&WindowHookProc),
        &module);
    return module;
}

void MarkStage(LONG stage) {
    if (!g_probe_view) {
        wchar_t name[96]{};
        wsprintfW(
            name,
            L"Local\\WindowTintProbe_%lu",
            GetCurrentProcessId());
        g_probe_mapping = CreateFileMappingW(
            INVALID_HANDLE_VALUE,
            nullptr,
            PAGE_READWRITE,
            0,
            sizeof(LONG),
            name);
        if (g_probe_mapping) {
            g_probe_view = static_cast<volatile LONG*>(
                MapViewOfFile(
                    g_probe_mapping,
                    FILE_MAP_WRITE,
                    0,
                    0,
                    sizeof(LONG)));
        }
    }
    if (g_probe_view) {
        InterlockedExchange(const_cast<LONG*>(g_probe_view), stage);
    }
}

bool EnsureGdiplus() {
    std::lock_guard<std::mutex> lock(g_gdiplus_mutex);
    if (g_gdiplus_ready) {
        return true;
    }
    Gdiplus::GdiplusStartupInput input;
    const Gdiplus::Status status =
        Gdiplus::GdiplusStartup(&g_gdiplus_token, &input, nullptr);
    g_gdiplus_ready = status == Gdiplus::Ok;
    return g_gdiplus_ready;
}

std::shared_ptr<OverlayState> FindState(HWND hwnd) {
    std::lock_guard<std::mutex> lock(g_state_mutex);
    const auto found = g_states.find(hwnd);
    return found == g_states.end() ? nullptr : found->second;
}

HookBinding* GetBinding(HWND hwnd) {
    return reinterpret_cast<HookBinding*>(
        GetPropW(hwnd, kStatePropertyName));
}

bool AttachBinding(
    HWND hwnd,
    const std::shared_ptr<OverlayState>& state) {
    auto* binding = new (std::nothrow) HookBinding;
    if (!binding) {
        return false;
    }
    binding->state = state;
    if (!SetPropW(hwnd, kStatePropertyName, binding)) {
        delete binding;
        return false;
    }
    return true;
}

void DetachBinding(HWND hwnd) {
    auto* binding = GetBinding(hwnd);
    if (!binding) {
        return;
    }
    RemovePropW(hwnd, kStatePropertyName);
    delete binding;
}

void RetireState(
    HWND hwnd,
    const std::shared_ptr<OverlayState>& state) {
    std::lock_guard<std::mutex> lock(g_state_mutex);
    const auto found = g_states.find(hwnd);
    if (found != g_states.end() && found->second == state) {
        g_states.erase(found);
    }
}

bool EnsureOverlayClass() {
    static std::once_flag once;
    static bool ready = false;
    std::call_once(once, [] {
        HMODULE module = CurrentModule();
        if (!module) {
            return;
        }
        WNDCLASSEXW window_class{};
        window_class.cbSize = sizeof(window_class);
        window_class.style = CS_HREDRAW | CS_VREDRAW;
        window_class.lpfnWndProc = OverlayWindowProc;
        window_class.hInstance = module;
        window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
        window_class.lpszClassName = kOverlayClassName;
        bool class_ready = RegisterClassExW(&window_class) != 0;
        if (!class_ready && GetLastError() == ERROR_CLASS_ALREADY_EXISTS) {
            WNDCLASSEXW existing{};
            existing.cbSize = sizeof(existing);
            class_ready =
                GetClassInfoExW(module, kOverlayClassName, &existing) &&
                existing.lpfnWndProc == OverlayWindowProc;
        }
        if (class_ready) {
            g_overlay_module = module;
            ready = true;
        }
    });
    return ready;
}

bool UploadOverlayImage(HWND overlay, Gdiplus::Image* image) {
    RECT client{};
    if (!image || !GetClientRect(overlay, &client)) {
        return false;
    }
    const int width = client.right - client.left;
    const int height = client.bottom - client.top;
    if (width <= 0 || height <= 0) {
        return false;
    }

    BITMAPINFO bitmap_info{};
    bitmap_info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bitmap_info.bmiHeader.biWidth = width;
    bitmap_info.bmiHeader.biHeight = -height;
    bitmap_info.bmiHeader.biPlanes = 1;
    bitmap_info.bmiHeader.biBitCount = 32;
    bitmap_info.bmiHeader.biCompression = BI_RGB;

    HDC memory_dc = CreateCompatibleDC(nullptr);
    void* dib_bits = nullptr;
    HBITMAP dib = memory_dc
                     ? CreateDIBSection(
                           memory_dc,
                           &bitmap_info,
                           DIB_RGB_COLORS,
                           &dib_bits,
                           nullptr,
                           0)
                     : nullptr;
    if (!memory_dc || !dib || !dib_bits) {
        if (dib) {
            DeleteObject(dib);
        }
        if (memory_dc) {
            DeleteDC(memory_dc);
        }
        return false;
    }

    Gdiplus::Status draw_status = Gdiplus::GenericError;
    {
        Gdiplus::Bitmap canvas(
            width,
            height,
            width * 4,
            PixelFormat32bppPARGB,
            static_cast<BYTE*>(dib_bits));
        if (canvas.GetLastStatus() == Gdiplus::Ok) {
            Gdiplus::Graphics graphics(&canvas);
            graphics.Clear(Gdiplus::Color(0, 0, 0, 0));
            graphics.SetCompositingMode(Gdiplus::CompositingModeSourceOver);
            graphics.SetCompositingQuality(Gdiplus::CompositingQualityHighQuality);
            graphics.SetInterpolationMode(Gdiplus::InterpolationModeHighQualityBicubic);
            graphics.SetPixelOffsetMode(Gdiplus::PixelOffsetModeHighQuality);
            draw_status = graphics.DrawImage(
                image,
                Gdiplus::Rect(0, 0, width, height),
                0,
                0,
                static_cast<INT>(image->GetWidth()),
                static_cast<INT>(image->GetHeight()),
                Gdiplus::UnitPixel);
            graphics.Flush(Gdiplus::FlushIntentionSync);
        }
    }
    if (draw_status != Gdiplus::Ok) {
        DeleteObject(dib);
        DeleteDC(memory_dc);
        return false;
    }

    HGDIOBJ previous = SelectObject(memory_dc, dib);
    POINT source_point{0, 0};
    SIZE size{width, height};
    BLENDFUNCTION blend{};
    blend.BlendOp = AC_SRC_OVER;
    blend.SourceConstantAlpha = 255;
    blend.AlphaFormat = AC_SRC_ALPHA;

    HDC screen_dc = GetDC(nullptr);
    const BOOL updated =
        screen_dc &&
        UpdateLayeredWindow(
            overlay,
            screen_dc,
            nullptr,
            &size,
            memory_dc,
            &source_point,
            0,
            &blend,
            ULW_ALPHA);
    if (screen_dc) {
        ReleaseDC(nullptr, screen_dc);
    }
    SelectObject(memory_dc, previous);
    DeleteObject(dib);
    DeleteDC(memory_dc);
    return updated == TRUE;
}

bool GetTargetFrameRect(HWND target, RECT* frame) {
    if (!frame) {
        return false;
    }
    const HRESULT result = DwmGetWindowAttribute(
        target,
        kDwmExtendedFrameBounds,
        frame,
        sizeof(*frame));
    if (SUCCEEDED(result)) {
        return true;
    }
    return GetWindowRect(target, frame) ? true : false;
}

void HideOverlay(const std::shared_ptr<OverlayState>& state) {
    if (state && state->overlay && IsWindow(state->overlay)) {
        ShowWindow(state->overlay, SW_HIDE);
    }
}

void ScheduleResizeSettle(HWND target, const std::shared_ptr<OverlayState>& state) {
    if (!state || !IsWindow(target)) {
        return;
    }
    const bool was_pending = state->resize_pending;
    state->resize_pending = true;
    if (!was_pending) {
        KillTimer(target, kOverlayTimerId);
        HideOverlay(state);
    }
    if (!state->interactive_resize) {
        SetTimer(target, kResizeSettleTimerId, kResizeSettleDelay, nullptr);
    }
}

void FinishResizeSettle(HWND target, const std::shared_ptr<OverlayState>& state) {
    KillTimer(target, kResizeSettleTimerId);
    if (state) {
        state->resize_pending = false;
        state->interactive_resize = false;
    }
    ShowOverlay(target, state, true);
}

struct OverlayCleanup {
    HWND target;
    HWND keep;
    DWORD process_id;
    std::vector<HWND> windows;
};

bool IsOverlayClassName(const wchar_t* class_name) {
    if (!class_name) {
        return false;
    }
    const size_t prefix_length = std::wcslen(kOverlayClassPrefix);
    if (std::wcsncmp(class_name, kOverlayClassPrefix, prefix_length) != 0) {
        return false;
    }
    return class_name[prefix_length] == L'\0' ||
        class_name[prefix_length] == L'_';
}

BOOL CALLBACK CollectTargetOverlays(HWND hwnd, LPARAM parameter) {
    auto* cleanup = reinterpret_cast<OverlayCleanup*>(parameter);
    if (!cleanup || hwnd == cleanup->keep) {
        return TRUE;
    }
    wchar_t class_name[64]{};
    if (GetClassNameW(hwnd, class_name, 64) <= 0 ||
        !IsOverlayClassName(class_name) ||
        GetWindowLongPtrW(hwnd, GWLP_HWNDPARENT) !=
            reinterpret_cast<LONG_PTR>(cleanup->target)) {
        return TRUE;
    }
    DWORD process_id = 0;
    GetWindowThreadProcessId(hwnd, &process_id);
    if (process_id == cleanup->process_id) {
        cleanup->windows.push_back(hwnd);
    }
    return TRUE;
}

void DestroyStrayOverlays(HWND target, HWND keep) {
    OverlayCleanup cleanup{
        target,
        keep,
        GetCurrentProcessId(),
        {},
    };
    EnumWindows(CollectTargetOverlays, reinterpret_cast<LPARAM>(&cleanup));
    for (HWND overlay : cleanup.windows) {
        DestroyWindow(overlay);
    }
}

bool ShowOverlay(HWND target, const std::shared_ptr<OverlayState>& state, bool force_upload) {
    MarkStage(4);
    if (!state || !IsWindow(target)) {
        return false;
    }
    if (state->showing) {
        return true;
    }
    state->showing = true;
    struct ShowingGuard {
        bool* showing;
        ~ShowingGuard() {
            *showing = false;
        }
    } showing_guard{&state->showing};

    if (!EnsureGdiplus() || !EnsureOverlayClass()) {
        return false;
    }
    MarkStage(5);

    if (!IsWindowVisible(target) || IsIconic(target)) {
        HideOverlay(state);
        return true;
    }

    RECT frame{};
    if (!GetTargetFrameRect(target, &frame)) {
        return false;
    }
    const int x = frame.left;
    const int y = frame.top;
    const int width = frame.right - frame.left;
    const int height = frame.bottom - frame.top;
    if (width <= 0 || height <= 0) {
        return false;
    }

    bool created = false;
    if (!state->overlay || !IsWindow(state->overlay)) {
        state->overlay = CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE |
                WS_EX_TOOLWINDOW,
            kOverlayClassName,
            L"",
            WS_POPUP | WS_VISIBLE | WS_DISABLED,
            x,
            y,
            width,
            height,
            target,
            nullptr,
            g_overlay_module,
            target);
        if (!state->overlay) {
            return false;
        }
        MarkStage(6);
        state->x = 0;
        state->y = 0;
        state->width = 0;
        state->height = 0;
        created = true;
    }

    const bool moved = state->x != x || state->y != y;
    const bool size_changed =
        created || state->width != width || state->height != height;
    if (moved || size_changed) {
        const bool target_topmost =
            (GetWindowLongPtrW(target, GWL_EXSTYLE) & WS_EX_TOPMOST) != 0;
        const bool overlay_topmost =
            (GetWindowLongPtrW(state->overlay, GWL_EXSTYLE) & WS_EX_TOPMOST) != 0;
        UINT flags =
            SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_SHOWWINDOW;
        if (!size_changed) {
            flags |= SWP_NOSIZE;
        }
        HWND insert_after = nullptr;
        if (target_topmost && !overlay_topmost) {
            insert_after = HWND_TOPMOST;
        } else if (!target_topmost && overlay_topmost) {
            insert_after = HWND_NOTOPMOST;
        } else {
            flags |= SWP_NOZORDER;
        }
        SetWindowPos(
            state->overlay,
            insert_after,
            x,
            y,
            width,
            height,
            flags);
        state->x = x;
        state->y = y;
        state->width = width;
        state->height = height;

        if (size_changed) {
            HRGN region =
                CreateRoundRectRgn(0, 0, width + 1, height + 1, 16, 16);
            if (region) {
                SetWindowRgn(state->overlay, region, TRUE);
            }
        }
    }
    if (!IsWindowVisible(state->overlay)) {
        ShowWindow(state->overlay, SW_SHOWNOACTIVATE);
    }
    if (force_upload) {
        KillTimer(target, kResizeSettleTimerId);
        state->resize_pending = false;
        state->interactive_resize = false;
    }
    if (force_upload || created) {
        DestroyStrayOverlays(target, state->overlay);
    }

    std::lock_guard<std::mutex> lock(state->image_mutex);
    if (force_upload || size_changed || state->image_dirty) {
        if (state->image_dirty || !state->image) {
            state->image.reset(
                Gdiplus::Image::FromFile(state->path.c_str(), FALSE));
            if (!state->image ||
                state->image->GetLastStatus() != Gdiplus::Ok ||
                state->image->GetWidth() == 0 ||
                state->image->GetHeight() == 0) {
                state->image.reset();
                return false;
            }
            MarkStage(7);
        }
        if (!UploadOverlayImage(state->overlay, state->image.get())) {
            return false;
        }
        MarkStage(8);
        state->image_dirty = false;
    }

    MarkStage(9);
    SetTimer(target, kOverlayTimerId, kOverlayTimerInterval, nullptr);
    return true;
}

bool RemoveOverlayOnUiThread(
    HWND target,
    const std::shared_ptr<OverlayState>& state) {
    if (!state) {
        return false;
    }
    auto* binding = GetBinding(target);
    if (binding) {
        binding->destroying = true;
    }
    RetireState(target, state);
    KillTimer(target, kOverlayTimerId);
    KillTimer(target, kResizeSettleTimerId);
    if (state->overlay && IsWindow(state->overlay)) {
        DestroyWindow(state->overlay);
        state->overlay = nullptr;
    }
    DestroyStrayOverlays(target, nullptr);
    if (state->original_proc && IsWindow(target)) {
        SetWindowLongPtrW(
            target,
            GWLP_WNDPROC,
            reinterpret_cast<LONG_PTR>(state->original_proc));
        state->original_proc = nullptr;
    }
    if (IsWindow(target)) {
        InvalidateRect(target, nullptr, FALSE);
    }
    DetachBinding(target);
    return true;
}

LRESULT CALLBACK OverlayWindowProc(
    HWND hwnd,
    UINT message,
    WPARAM w_param,
    LPARAM l_param) {
    switch (message) {
        case WM_NCCREATE: {
            const auto* create = reinterpret_cast<const CREATESTRUCTW*>(l_param);
            SetWindowLongPtrW(
                hwnd,
                GWLP_USERDATA,
                reinterpret_cast<LONG_PTR>(create->lpCreateParams));
            return TRUE;
        }
        case WM_NCHITTEST:
            return HTTRANSPARENT;
        case WM_MOUSEACTIVATE:
            return MA_NOACTIVATE;
        case WM_ERASEBKGND:
            return TRUE;
        case WM_PAINT:
            ValidateRect(hwnd, nullptr);
            return 0;
        default:
            return DefWindowProcW(hwnd, message, w_param, l_param);
    }
}

LRESULT CALLBACK WindowHookProc(
    HWND hwnd,
    UINT message,
    WPARAM w_param,
    LPARAM l_param) {
    auto* binding = GetBinding(hwnd);
    if (!binding || !binding->state) {
        return DefWindowProcW(hwnd, message, w_param, l_param);
    }
    const auto state = binding->state;
    const WNDPROC original_proc = state->original_proc;
    if (!original_proc || binding->destroying) {
        return original_proc
            ? CallWindowProcW(original_proc, hwnd, message, w_param, l_param)
            : DefWindowProcW(hwnd, message, w_param, l_param);
    }

    switch (message) {
        case kApplyOverlayMessage:
            return ShowOverlay(hwnd, state, true) ? TRUE : FALSE;
        case kRemoveMessage:
            return RemoveOverlayOnUiThread(hwnd, state) ? TRUE : FALSE;
        case WM_DPICHANGED: {
            const LRESULT result =
                CallWindowProcW(original_proc, hwnd, message, w_param, l_param);
            if (!state->resize_pending) {
                ShowOverlay(hwnd, state, false);
            }
            return result;
        }
        case WM_ENTERSIZEMOVE:
            state->interactive_resize = true;
            break;
        case WM_EXITSIZEMOVE: {
            const LRESULT result =
                CallWindowProcW(original_proc, hwnd, message, w_param, l_param);
            if (state->resize_pending) {
                FinishResizeSettle(hwnd, state);
            } else {
                state->interactive_resize = false;
            }
            return result;
        }
        case WM_SHOWWINDOW:
            if (w_param) {
                if (!state->resize_pending) {
                    ShowOverlay(hwnd, state, false);
                }
            } else {
                HideOverlay(state);
            }
            break;
        case WM_WINDOWPOSCHANGED: {
            const auto* window_pos =
                reinterpret_cast<const WINDOWPOS*>(l_param);
            const bool size_changed =
                window_pos && !(window_pos->flags & SWP_NOSIZE);
            const LRESULT result =
                CallWindowProcW(original_proc, hwnd, message, w_param, l_param);
            if (size_changed) {
                ScheduleResizeSettle(hwnd, state);
            } else if (!state->resize_pending) {
                ShowOverlay(hwnd, state, false);
            }
            return result;
        }
        case WM_MOVE: {
            const LRESULT result =
                CallWindowProcW(original_proc, hwnd, message, w_param, l_param);
            if (!state->resize_pending) {
                ShowOverlay(hwnd, state, false);
            }
            return result;
        }
        case WM_SIZE:
            if (w_param == SIZE_MINIMIZED) {
                state->resize_pending = true;
                state->interactive_resize = false;
                KillTimer(hwnd, kOverlayTimerId);
                KillTimer(hwnd, kResizeSettleTimerId);
                HideOverlay(state);
                break;
            }
            {
                const LRESULT result =
                    CallWindowProcW(original_proc, hwnd, message, w_param, l_param);
                ScheduleResizeSettle(hwnd, state);
                return result;
            }
        case WM_TIMER:
            if (w_param == kResizeSettleTimerId) {
                if (!state->interactive_resize) {
                    FinishResizeSettle(hwnd, state);
                }
                return 0;
            }
            if (w_param == kOverlayTimerId) {
                if (!state->resize_pending) {
                    ShowOverlay(hwnd, state, false);
                }
                return 0;
            }
            break;
        case WM_NCDESTROY: {
            binding->destroying = true;
            KillTimer(hwnd, kOverlayTimerId);
            KillTimer(hwnd, kResizeSettleTimerId);
            RetireState(hwnd, state);
            const LRESULT result = CallWindowProcW(
                original_proc,
                hwnd,
                message,
                w_param,
                l_param);
            DetachBinding(hwnd);
            return result;
        }
        default:
            break;
    }
    return CallWindowProcW(original_proc, hwnd, message, w_param, l_param);
}

unsigned __stdcall OwnerWatchThread(void* parameter) {
    std::unique_ptr<OwnerWatch> watch(static_cast<OwnerWatch*>(parameter));
    if (!watch || !watch->process) {
        return 0;
    }

    const DWORD wait_result = WaitForSingleObject(watch->process, INFINITE);
    bool should_cleanup = false;
    {
        std::lock_guard<std::mutex> lock(g_owner_mutex);
        if (watch->generation == g_owner_generation) {
            should_cleanup = wait_result == WAIT_OBJECT_0;
            g_owner_process = nullptr;
            g_owner_pid = 0;
        }
    }

    if (should_cleanup) {
        std::vector<HWND> windows;
        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            windows.reserve(g_states.size());
            for (const auto& entry : g_states) {
                windows.push_back(entry.first);
            }
        }
        for (HWND hwnd : windows) {
            PostMessage(hwnd, kRemoveMessage, 0, 0);
        }
    }

    CloseHandle(watch->process);
    return 0;
}

bool EnsureOwnerWatcher(DWORD pid) {
    if (pid == 0) {
        return false;
    }
    std::lock_guard<std::mutex> lock(g_owner_mutex);
    if (g_owner_process && g_owner_pid == pid) {
        return true;
    }

    if (g_owner_process) {
        ++g_owner_generation;
        CloseHandle(g_owner_process);
        g_owner_process = nullptr;
        g_owner_pid = 0;
    }

    HANDLE process = OpenProcess(SYNCHRONIZE, FALSE, pid);
    if (!process) {
        return false;
    }
    auto watch = std::make_unique<OwnerWatch>();
    watch->process = process;
    watch->pid = pid;
    watch->generation = ++g_owner_generation;
    const uintptr_t thread = _beginthreadex(
        nullptr,
        0,
        OwnerWatchThread,
        watch.get(),
        0,
        nullptr);
    if (thread == 0) {
        CloseHandle(process);
        return false;
    }
    CloseHandle(reinterpret_cast<HANDLE>(thread));
    g_owner_process = process;
    g_owner_pid = pid;
    watch.release();
    return true;
}

BOOL ApplyWithOwner(HWND hwnd, LPCWSTR path, DWORD owner_pid) {
    if (!Apply(hwnd, path)) {
        return FALSE;
    }
    EnsureOwnerWatcher(owner_pid);
    return TRUE;
}

}  // namespace

extern "C" __declspec(dllexport) BOOL WINAPI Apply(HWND hwnd, LPCWSTR path) {
    MarkStage(1);
    if (!IsWindow(hwnd) || path == nullptr || path[0] == L'\0') {
        return FALSE;
    }

    std::shared_ptr<OverlayState> state;
    bool install_proc = false;
    {
        std::lock_guard<std::mutex> lock(g_state_mutex);
        const auto found = g_states.find(hwnd);
        if (found != g_states.end()) {
            state = found->second;
        } else {
            state = std::make_shared<OverlayState>();
            state->target = hwnd;
            install_proc = true;
        }
    }

    {
        std::lock_guard<std::mutex> lock(state->image_mutex);
        state->path = path;
        state->image_dirty = true;
    }

    if (install_proc) {
        // Every older DLL owns one entry in the window-procedure chain.
        for (int attempt = 0; attempt < 16; ++attempt) {
            DWORD_PTR result = 0;
            const LRESULT sent = SendMessageTimeoutW(
                hwnd,
                kRemoveMessage,
                0,
                0,
                SMTO_ABORTIFHUNG | SMTO_BLOCK,
                1000,
                &result);
            if (!sent) {
                break;
            }
        }

        const WNDPROC original_proc = reinterpret_cast<WNDPROC>(
            GetWindowLongPtrW(hwnd, GWLP_WNDPROC));
        if (!original_proc) {
            return FALSE;
        }
        state->original_proc = original_proc;
        if (!AttachBinding(hwnd, state)) {
            state->original_proc = nullptr;
            return FALSE;
        }
        SetLastError(ERROR_SUCCESS);
        const WNDPROC replaced_proc = reinterpret_cast<WNDPROC>(
            SetWindowLongPtrW(
                hwnd,
                GWLP_WNDPROC,
                reinterpret_cast<LONG_PTR>(WindowHookProc)));
        if (!replaced_proc) {
            DetachBinding(hwnd);
            state->original_proc = nullptr;
            return FALSE;
        }
        state->original_proc = replaced_proc;
        MarkStage(2);
        {
            std::lock_guard<std::mutex> lock(g_state_mutex);
            g_states.emplace(hwnd, state);
        }
    }
    const BOOL posted =
        PostMessage(hwnd, kApplyOverlayMessage, 0, 0) ? TRUE : FALSE;
    if (posted) {
        MarkStage(3);
    }
    return posted;
}

extern "C" __declspec(dllexport) BOOL WINAPI Remove(HWND hwnd) {
    if (!IsWindow(hwnd)) {
        return TRUE;
    }
    return PostMessage(hwnd, kRemoveMessage, 0, 0) ? TRUE : FALSE;
}

extern "C" __declspec(dllexport) DWORD WINAPI ApplyRemote(LPVOID parameter) {
    struct Request {
        HWND hwnd;
        WCHAR path[kMaxLongPath];
        DWORD owner_pid;
    };
    if (parameter == nullptr) {
        return FALSE;
    }
    const auto* request = static_cast<const Request*>(parameter);
    const std::wstring path(request->path, request->path + kMaxLongPath);
    if (path.empty() || path[0] == L'\0') {
        return FALSE;
    }
    return ApplyWithOwner(request->hwnd, path.c_str(), request->owner_pid) ? TRUE : FALSE;
}

extern "C" __declspec(dllexport) DWORD WINAPI RemoveRemote(LPVOID parameter) {
    if (parameter == nullptr) {
        return FALSE;
    }
    const HWND hwnd = *static_cast<const HWND*>(parameter);
    return Remove(hwnd) ? TRUE : FALSE;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(instance);
    }
    return TRUE;
}
