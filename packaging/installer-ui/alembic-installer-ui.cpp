#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#define _WIN32_WINNT 0x0601
#include <windows.h>
#include <Unknwn.h>
#include <windowsx.h>
#include <commctrl.h>
#include <dwmapi.h>
#include <gdiplus.h>
#include <shobjidl.h>
#include <shlobj.h>
#include <atomic>
#include <algorithm>
#include <iterator>
#include <memory>
#include <string>
#include <cstdio>
#include <cstdlib>

#pragma comment(lib, "gdiplus.lib")
#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "dwmapi.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shell32.lib")

// Stack node layout used by the NSIS plug-in ABI (see exdll.h).
struct stack_t {
    stack_t* next;
    wchar_t text[1];
};

namespace {
constexpr wchar_t kWindowClass[] = L"Alembic.NativeInstaller.UI";
constexpr UINT kUiUpdate = WM_APP + 17;
constexpr UINT kUiShutdown = WM_APP + 18;
constexpr int kWidth = 560;
constexpr int kHeight = 390;
constexpr int kWelcome = 0;
constexpr int kPreparing = 1;
constexpr int kExtracting = 2;
constexpr int kFinalizing = 3;
constexpr int kComplete = 4;
constexpr UINT kPbmSetRange = WM_USER + 1;
constexpr UINT kPbmSetPos = WM_USER + 2;
constexpr UINT kPbmDeltaPos = WM_USER + 3;
constexpr UINT kPbmSetStep = WM_USER + 4;
constexpr UINT kPbmStepIt = WM_USER + 5;
constexpr UINT kPbmSetRange32 = WM_USER + 6;
// Cold start of a freshly extracted, unsigned DLL (GDI+ load, font enumeration,
// anti-virus scan) can exceed a few seconds; give it real headroom.
constexpr DWORD kStartupTimeoutMs = 15000;

struct Palette {
    Gdiplus::Color paper;
    Gdiplus::Color paperDeep;
    Gdiplus::Color card;
    Gdiplus::Color ink;
    Gdiplus::Color muted;
    Gdiplus::Color emerald;
    Gdiplus::Color border;
    Gdiplus::Color field;
};

struct SharedState {
    HMODULE module = nullptr;
    HWND installer = nullptr;
    std::atomic<HWND> window{nullptr};
    std::atomic<HWND> directoryEdit{nullptr};
    HANDLE uiThread = nullptr;
    DWORD uiThreadId = 0;
    HHOOK progressHook = nullptr;
    HANDLE readyEvent = nullptr;
    std::atomic<bool> cancelled{false};
    std::atomic<bool> foregroundSet{false};
    std::atomic<int> mode{kWelcome};
    std::atomic<int> rawProgress{0};
    std::atomic<bool> progressKnown{false};
    std::atomic<int> progressMinimum{0};
    std::atomic<int> progressMaximum{100};
    std::atomic<int> progressStep{10};
    std::atomic<bool> desktopShortcut{true};
    std::atomic<bool> startMenuShortcut{true};
    std::wstring initialDirectory;
    std::wstring optionsPath;
    bool dark = false;
    ULONG_PTR gdiplusToken = 0;
    std::unique_ptr<Gdiplus::FontFamily> fontFamily;
    HFONT editFont = nullptr;
    HBRUSH editBrush = nullptr;
    HBRUSH backgroundBrush = nullptr;
};

SharedState g;

Gdiplus::Color Rgb(BYTE r, BYTE green, BYTE b) {
    return Gdiplus::Color(255, r, green, b);
}

Palette CurrentPalette() {
    if (g.dark) {
        return {
            Rgb(18, 23, 20), Rgb(24, 32, 25), Rgb(28, 36, 32),
            Rgb(230, 236, 225), Rgb(169, 182, 161), Rgb(99, 185, 140),
            Rgb(44, 56, 48), Rgb(31, 40, 35)
        };
    }
    return {
        Rgb(250, 248, 241), Rgb(244, 242, 232), Rgb(255, 253, 248),
        Rgb(76, 85, 73), Rgb(101, 115, 95), Rgb(57, 113, 87),
        Rgb(222, 223, 212), Rgb(255, 255, 252)
    };
}

void FillRounded(Gdiplus::Graphics& graphics, const Gdiplus::RectF& rect,
                 float radius, const Gdiplus::Color& color) {
    Gdiplus::GraphicsPath path;
    const float diameter = radius * 2.0f;
    path.AddArc(rect.X, rect.Y, diameter, diameter, 180.0f, 90.0f);
    path.AddArc(rect.GetRight() - diameter, rect.Y, diameter, diameter, 270.0f, 90.0f);
    path.AddArc(rect.GetRight() - diameter, rect.GetBottom() - diameter, diameter, diameter, 0.0f, 90.0f);
    path.AddArc(rect.X, rect.GetBottom() - diameter, diameter, diameter, 90.0f, 90.0f);
    path.CloseFigure();
    Gdiplus::SolidBrush brush(color);
    graphics.FillPath(&brush, &path);
}

void StrokeRounded(Gdiplus::Graphics& graphics, const Gdiplus::RectF& rect,
                   float radius, const Gdiplus::Color& color, float width = 1.0f) {
    Gdiplus::GraphicsPath path;
    const float diameter = radius * 2.0f;
    path.AddArc(rect.X, rect.Y, diameter, diameter, 180.0f, 90.0f);
    path.AddArc(rect.GetRight() - diameter, rect.Y, diameter, diameter, 270.0f, 90.0f);
    path.AddArc(rect.GetRight() - diameter, rect.GetBottom() - diameter, diameter, diameter, 0.0f, 90.0f);
    path.AddArc(rect.X, rect.GetBottom() - diameter, diameter, diameter, 90.0f, 90.0f);
    path.CloseFigure();
    Gdiplus::Pen pen(color, width);
    graphics.DrawPath(&pen, &path);
}

void DrawUiText(Gdiplus::Graphics& graphics, const wchar_t* text, float x, float y,
              float width, float height, float size, const Gdiplus::Color& color,
              Gdiplus::FontStyle style = Gdiplus::FontStyleRegular,
              Gdiplus::StringAlignment alignment = Gdiplus::StringAlignmentNear) {
    // Enumerating font families on every text run makes the first paint take
    // seconds; the family is created once on the UI thread and reused.
    std::unique_ptr<Gdiplus::FontFamily> fallback;
    Gdiplus::FontFamily* family = g.fontFamily.get();
    if (!family) {
        fallback.reset(new Gdiplus::FontFamily(L"Microsoft YaHei UI"));
        if (fallback->GetLastStatus() != Gdiplus::Ok) {
            fallback.reset(new Gdiplus::FontFamily(L"Microsoft YaHei"));
        }
        family = fallback.get();
    }
    Gdiplus::Font font(family, size, style, Gdiplus::UnitPixel);
    Gdiplus::SolidBrush brush(color);
    Gdiplus::StringFormat format;
    format.SetAlignment(alignment);
    format.SetLineAlignment(Gdiplus::StringAlignmentCenter);
    format.SetTrimming(Gdiplus::StringTrimmingEllipsisCharacter);
    Gdiplus::RectF rect(x, y, width, height);
    graphics.DrawString(text, -1, &font, rect, &format, &brush);
}

bool ReadDarkMode() {
    DWORD value = 1;
    DWORD bytes = sizeof(value);
    if (RegGetValueW(HKEY_CURRENT_USER,
        L"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",
        L"AppsUseLightTheme", RRF_RT_REG_DWORD, nullptr, &value, &bytes) != ERROR_SUCCESS) {
        return false;
    }
    return value == 0;
}

RECT WelcomeInstallButton() { return {356, 322, 524, 366}; }
RECT WelcomeBrowseButton() { return {444, 157, 524, 195}; }
RECT DesktopOption() { return {36, 229, 266, 258}; }
RECT StartMenuOption() { return {36, 270, 266, 299}; }
RECT MinimizeButton() { return {478, 10, 516, 48}; }
RECT CloseButton() { return {516, 10, 550, 48}; }
RECT FinishButton() { return {388, 322, 524, 366}; }

bool Inside(const RECT& rect, POINT point) {
    return point.x >= rect.left && point.x < rect.right &&
           point.y >= rect.top && point.y < rect.bottom;
}

void DrawCheckbox(Gdiplus::Graphics& graphics, const Palette& p, int y,
                  bool checked, const wchar_t* label) {
    Gdiplus::RectF box(37.0f, static_cast<float>(y), 18.0f, 18.0f);
    FillRounded(graphics, box, 5.0f, checked ? p.emerald : p.card);
    if (!checked) StrokeRounded(graphics, box, 5.0f, p.border, 1.0f);
    else {
        Gdiplus::Pen pen(g.dark ? Rgb(15, 21, 18) : Rgb(251, 250, 243), 2.0f);
        pen.SetStartCap(Gdiplus::LineCapRound);
        pen.SetEndCap(Gdiplus::LineCapRound);
        graphics.DrawLine(&pen, 41.0f, y + 9.0f, 45.0f, y + 13.0f);
        graphics.DrawLine(&pen, 45.0f, y + 13.0f, 52.0f, y + 5.0f);
    }
    DrawUiText(graphics, label, 67.0f, y - 4.0f, 300.0f, 27.0f, 14.0f, p.ink);
}

void DrawChrome(Gdiplus::Graphics& graphics, const Palette& p) {
    FillRounded(graphics, Gdiplus::RectF(0, 0, kWidth, kHeight), 12.0f, p.paper);
    Gdiplus::SolidBrush header(p.paperDeep);
    graphics.FillRectangle(&header, 0.0f, 0.0f, static_cast<float>(kWidth), 58.0f);
    Gdiplus::SolidBrush accent(p.emerald);
    graphics.FillRectangle(&accent, 0.0f, 0.0f, 3.0f, 58.0f);
    Gdiplus::Pen separator(p.border, 1.0f);
    graphics.DrawLine(&separator, 0.0f, 57.0f, static_cast<float>(kWidth), 57.0f);
    DrawUiText(graphics, L"ALEMBIC", 28.0f, 8.0f, 230.0f, 40.0f, 15.0f, p.ink, Gdiplus::FontStyleBold);

    const Gdiplus::Color buttonHover = g.dark ? Rgb(35, 45, 39) : Rgb(233, 235, 225);
    if (GetCapture() == g.window) {
        POINT cursor{};
        GetCursorPos(&cursor);
        ScreenToClient(g.window, &cursor);
        if (Inside(MinimizeButton(), cursor))
            FillRounded(graphics, Gdiplus::RectF(478, 10, 38, 38), 8.0f, buttonHover);
        if (Inside(CloseButton(), cursor))
            FillRounded(graphics, Gdiplus::RectF(516, 10, 34, 38), 8.0f, g.dark ? Rgb(75, 45, 42) : Rgb(245, 226, 218));
    }
    Gdiplus::Pen iconPen(p.muted, 1.6f);
    iconPen.SetStartCap(Gdiplus::LineCapRound);
    iconPen.SetEndCap(Gdiplus::LineCapRound);
    graphics.DrawLine(&iconPen, 490.0f, 29.0f, 504.0f, 29.0f);
    Gdiplus::Pen closePen(p.ink, 1.5f);
    closePen.SetStartCap(Gdiplus::LineCapRound);
    closePen.SetEndCap(Gdiplus::LineCapRound);
    graphics.DrawLine(&closePen, 527.0f, 21.0f, 539.0f, 33.0f);
    graphics.DrawLine(&closePen, 539.0f, 21.0f, 527.0f, 33.0f);
}

void DrawWelcome(Gdiplus::Graphics& graphics, const Palette& p) {
    DrawUiText(graphics, L"安装 ALEMBIC", 36.0f, 77.0f, 440.0f, 36.0f, 22.0f, p.ink, Gdiplus::FontStyleBold);
    DrawUiText(graphics, L"安装位置", 36.0f, 126.0f, 130.0f, 22.0f, 13.0f, p.muted, Gdiplus::FontStyleBold);

    FillRounded(graphics, Gdiplus::RectF(36.0f, 157.0f, 388.0f, 38.0f), 8.0f, p.field);
    StrokeRounded(graphics, Gdiplus::RectF(36.0f, 157.0f, 388.0f, 38.0f), 8.0f, p.border);
    FillRounded(graphics, Gdiplus::RectF(444.0f, 157.0f, 80.0f, 38.0f), 8.0f, p.paperDeep);
    StrokeRounded(graphics, Gdiplus::RectF(444.0f, 157.0f, 80.0f, 38.0f), 8.0f, p.border);
    DrawUiText(graphics, L"浏览…", 444.0f, 157.0f, 80.0f, 38.0f, 13.0f, p.ink, Gdiplus::FontStyleRegular, Gdiplus::StringAlignmentCenter);

    DrawUiText(graphics, L"快捷方式", 36.0f, 205.0f, 130.0f, 22.0f, 13.0f, p.muted, Gdiplus::FontStyleBold);
    DrawCheckbox(graphics, p, 232, g.desktopShortcut.load(), L"桌面快捷方式");
    DrawCheckbox(graphics, p, 273, g.startMenuShortcut.load(), L"开始菜单快捷方式");

    Gdiplus::Pen separator(p.border, 1.0f);
    graphics.DrawLine(&separator, 28.0f, 309.0f, 532.0f, 309.0f);
    FillRounded(graphics, Gdiplus::RectF(356.0f, 322.0f, 168.0f, 44.0f), 10.0f, p.emerald);
    DrawUiText(graphics, L"安装", 356.0f, 322.0f, 168.0f, 44.0f, 14.0f,
        g.dark ? Rgb(15, 21, 18) : Rgb(251, 250, 243), Gdiplus::FontStyleBold,
        Gdiplus::StringAlignmentCenter);
}

int animationOffset = 0;

void DrawProgress(Gdiplus::Graphics& graphics, const Palette& p) {
    const int mode = g.mode.load();
    const wchar_t* status = mode == kPreparing ? L"正在准备安装…" :
        mode == kFinalizing ? L"正在配置运行环境…" : L"正在安装 ALEMBIC…";
    DrawUiText(graphics, L"安装 ALEMBIC", 36.0f, 94.0f, 440.0f, 35.0f, 22.0f, p.ink, Gdiplus::FontStyleBold);
    DrawUiText(graphics, status, 36.0f, 166.0f, 488.0f, 28.0f, 14.0f, p.muted);

    const float barX = 36.0f, barY = 210.0f, barW = 488.0f, barH = 10.0f;
    const Gdiplus::RectF track(barX, barY, barW, barH);
    FillRounded(graphics, track, 5.0f, p.border);
    const bool hasRealProgress = mode == kExtracting && g.progressKnown.load();
    if (hasRealProgress) {
        const int progress = std::max(0, std::min(100, g.rawProgress.load()));
        if (progress > 0) {
            FillRounded(graphics, Gdiplus::RectF(barX, barY,
                std::max(10.0f, barW * progress / 100.0f), barH), 5.0f, p.emerald);
        }
        wchar_t percent[8]{};
        wsprintfW(percent, L"%d%%", progress);
        DrawUiText(graphics, percent, 36.0f, 229.0f, 488.0f, 22.0f, 12.0f, p.muted,
            Gdiplus::FontStyleRegular, Gdiplus::StringAlignmentFar);
        return;
    }

    Gdiplus::GraphicsPath clip;
    const float diameter = barH;
    clip.AddArc(track.X, track.Y, diameter, diameter, 90.0f, 180.0f);
    clip.AddArc(track.GetRight() - diameter, track.Y, diameter, diameter, 270.0f, 180.0f);
    clip.CloseFigure();
    graphics.SetClip(&clip);
    const float segmentW = 104.0f;
    const float segmentX = barX + static_cast<float>(animationOffset) - segmentW;
    Gdiplus::SolidBrush segment(p.emerald);
    graphics.FillRectangle(&segment, segmentX, barY, segmentW, barH);
    graphics.ResetClip();
    animationOffset = (animationOffset + 5) % static_cast<int>(barW + segmentW);
}

void DrawComplete(Gdiplus::Graphics& graphics, const Palette& p) {
    DrawUiText(graphics, L"安装完成", 36.0f, 106.0f, 440.0f, 42.0f, 24.0f, p.ink, Gdiplus::FontStyleBold);
    DrawUiText(graphics, L"ALEMBIC 已安装。", 36.0f, 161.0f, 440.0f, 28.0f, 14.0f, p.muted);
    FillRounded(graphics, Gdiplus::RectF(388.0f, 322.0f, 136.0f, 44.0f), 10.0f, p.emerald);
    DrawUiText(graphics, L"完成", 388.0f, 322.0f, 136.0f, 44.0f, 14.0f,
        g.dark ? Rgb(15, 21, 18) : Rgb(251, 250, 243), Gdiplus::FontStyleBold,
        Gdiplus::StringAlignmentCenter);
}

void CreateDirectoryEdit(HWND window) {
    g.editBrush = CreateSolidBrush(g.dark ? RGB(31, 40, 35) : RGB(255, 255, 252));
    g.editFont = CreateFontW(-16, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
        DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
    g.directoryEdit = CreateWindowExW(0, L"EDIT", g.initialDirectory.c_str(),
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL,
        48, 164, 364, 24, window, reinterpret_cast<HMENU>(1001), g.module, nullptr);
    if (g.directoryEdit && g.editFont) SendMessageW(g.directoryEdit, WM_SETFONT, reinterpret_cast<WPARAM>(g.editFont), TRUE);
}

void ChooseFolder(HWND owner) {
    IFileOpenDialog* dialog = nullptr;
    HRESULT hr = CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER,
        IID_PPV_ARGS(&dialog));
    if (FAILED(hr) || !dialog) return;
    DWORD options = 0;
    if (SUCCEEDED(dialog->GetOptions(&options))) {
        dialog->SetOptions(options | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST);
    }
    if (SUCCEEDED(dialog->Show(owner))) {
        IShellItem* item = nullptr;
        if (SUCCEEDED(dialog->GetResult(&item)) && item) {
            PWSTR path = nullptr;
            if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path)) && path) {
                SetWindowTextW(g.directoryEdit, path);
                CoTaskMemFree(path);
            }
            item->Release();
        }
    }
    dialog->Release();
}

void DispatchWizardCommand(WORD command) {
    if (g.installer) PostMessageW(g.installer, WM_COMMAND, MAKEWPARAM(command, BN_CLICKED), 0);
}

// Hands the page result back to NSIS through one shared INI file. It is written as
// UTF-16LE with a BOM, exactly like NSIS' own WriteINIStr, so non-ASCII folder names
// survive. NSIS reads it with ReadINIStr; no Unicode buffer crosses the plugin ABI.
void PersistOptions() {
    if (g.optionsPath.empty()) return;
    std::wstring directory = g.initialDirectory;
    const HWND edit = g.directoryEdit.load();
    if (edit) {
        const int length = GetWindowTextLengthW(edit);
        directory.assign(static_cast<size_t>(length) + 1, L'\0');
        const int copied = GetWindowTextW(edit, &directory[0], length + 1);
        directory.resize(static_cast<size_t>(copied > 0 ? copied : 0));
    }
    while (directory.size() > 3 && (directory.back() == L'\\' || directory.back() == L'/')) {
        directory.pop_back();
    }
    std::wstring contents = L"\ufeff[install]\r\ndirectory=";
    contents += directory;
    contents += L"\r\ndesktop=";
    contents += g.desktopShortcut.load() ? L"1" : L"0";
    contents += L"\r\nstartmenu=";
    contents += g.startMenuShortcut.load() ? L"1" : L"0";
    contents += L"\r\n";

    HANDLE file = CreateFileW(g.optionsPath.c_str(), GENERIC_WRITE, 0, nullptr,
        CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return;
    DWORD written = 0;
    WriteFile(file, contents.data(), static_cast<DWORD>(contents.size() * sizeof(wchar_t)),
        &written, nullptr);
    CloseHandle(file);
}

// --- GDI renderer -----------------------------------------------------------
// The wizard is drawn with plain GDI on purpose: GDI+ start-up and font-family
// enumeration can block for a long time inside a cold, elevated installer
// process, which leaves the window unpainted and unresponsive.
HFONT g_fontTitle = nullptr;
HFONT g_fontBody = nullptr;
HFONT g_fontSmall = nullptr;

HFONT MakeUiFont(int height, bool bold) {
    return CreateFontW(-height, 0, 0, 0, bold ? FW_BOLD : FW_NORMAL, FALSE, FALSE, FALSE,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
        DEFAULT_PITCH | FF_DONTCARE, L"Microsoft YaHei UI");
}

void DestroyUiFonts() {
    if (g_fontTitle) { DeleteObject(g_fontTitle); g_fontTitle = nullptr; }
    if (g_fontBody) { DeleteObject(g_fontBody); g_fontBody = nullptr; }
    if (g_fontSmall) { DeleteObject(g_fontSmall); g_fontSmall = nullptr; }
}

void GdiFill(HDC dc, int left, int top, int right, int bottom, COLORREF color, int radius) {
    HBRUSH brush = CreateSolidBrush(color);
    HGDIOBJ oldBrush = SelectObject(dc, brush);
    HGDIOBJ oldPen = SelectObject(dc, GetStockObject(NULL_PEN));
    if (radius > 0) {
        RoundRect(dc, left, top, right, bottom, radius, radius);
    } else {
        Rectangle(dc, left, top, right, bottom);
    }
    SelectObject(dc, oldPen);
    SelectObject(dc, oldBrush);
    DeleteObject(brush);
}

void GdiFrame(HDC dc, int left, int top, int right, int bottom, COLORREF color, int radius) {
    HPEN pen = CreatePen(PS_SOLID, 1, color);
    HGDIOBJ oldPen = SelectObject(dc, pen);
    HGDIOBJ oldBrush = SelectObject(dc, GetStockObject(HOLLOW_BRUSH));
    if (radius > 0) {
        RoundRect(dc, left, top, right, bottom, radius, radius);
    } else {
        Rectangle(dc, left, top, right, bottom);
    }
    SelectObject(dc, oldBrush);
    SelectObject(dc, oldPen);
    DeleteObject(pen);
}

void GdiText(HDC dc, HFONT font, COLORREF color, const wchar_t* text,
             int left, int top, int right, int bottom, UINT format) {
    HGDIOBJ old = SelectObject(dc, font);
    SetTextColor(dc, color);
    SetBkMode(dc, TRANSPARENT);
    RECT rc{left, top, right, bottom};
    DrawTextW(dc, text, -1, &rc, format | DT_SINGLELINE | DT_VCENTER);
    SelectObject(dc, old);
}

void RenderGdi(HDC dc) {
    const bool dark = g.dark;
    const COLORREF paper = dark ? RGB(18, 23, 20) : RGB(250, 248, 241);
    const COLORREF paperDeep = dark ? RGB(24, 32, 25) : RGB(244, 242, 232);
    const COLORREF card = dark ? RGB(28, 36, 32) : RGB(255, 253, 248);
    const COLORREF ink = dark ? RGB(230, 236, 225) : RGB(76, 85, 73);
    const COLORREF muted = dark ? RGB(169, 182, 161) : RGB(101, 115, 95);
    const COLORREF emerald = dark ? RGB(99, 185, 140) : RGB(57, 113, 87);
    const COLORREF border = dark ? RGB(44, 56, 48) : RGB(222, 223, 212);
    const COLORREF field = dark ? RGB(31, 40, 35) : RGB(255, 255, 252);
    const COLORREF onEmerald = dark ? RGB(15, 21, 18) : RGB(251, 250, 243);

    GdiFill(dc, 0, 0, kWidth, kHeight, paper, 0);
    GdiFill(dc, 0, 0, kWidth, 58, paperDeep, 0);
    GdiFill(dc, 0, 0, 3, 58, emerald, 0);
    GdiText(dc, g_fontTitle, ink, L"ALEMBIC", 28, 8, 258, 48, DT_LEFT);
    GdiFill(dc, 0, 56, kWidth, 58, border, 0);

    // Window buttons: minimise bar and close cross.
    {
        HPEN pen = CreatePen(PS_SOLID, 2, muted);
        HGDIOBJ oldPen = SelectObject(dc, pen);
        MoveToEx(dc, 490, 29, nullptr);
        LineTo(dc, 504, 29);
        SelectObject(dc, oldPen);
        DeleteObject(pen);
    }
    {
        HPEN pen = CreatePen(PS_SOLID, 2, ink);
        HGDIOBJ oldPen = SelectObject(dc, pen);
        MoveToEx(dc, 527, 21, nullptr);
        LineTo(dc, 539, 33);
        MoveToEx(dc, 539, 21, nullptr);
        LineTo(dc, 527, 33);
        SelectObject(dc, oldPen);
        DeleteObject(pen);
    }

    const int mode = g.mode.load();
    if (mode == kWelcome) {
        GdiText(dc, g_fontTitle, ink, L"安装 ALEMBIC", 36, 77, 476, 113, DT_LEFT);
        GdiText(dc, g_fontSmall, muted, L"安装位置", 36, 126, 166, 148, DT_LEFT);

        GdiFill(dc, 36, 157, 424, 195, field, 8);
        GdiFrame(dc, 36, 157, 424, 195, border, 8);
        GdiFill(dc, 444, 157, 524, 195, paperDeep, 8);
        GdiFrame(dc, 444, 157, 524, 195, border, 8);
        GdiText(dc, g_fontSmall, ink, L"浏览…", 444, 157, 524, 195, DT_CENTER);

        GdiText(dc, g_fontSmall, muted, L"快捷方式", 36, 205, 166, 227, DT_LEFT);

        const struct { int top; bool checked; const wchar_t* label; } options[] = {
            {232, g.desktopShortcut.load(), L"桌面快捷方式"},
            {273, g.startMenuShortcut.load(), L"开始菜单快捷方式"},
        };
        for (const auto& option : options) {
            GdiFill(dc, 37, option.top, 55, option.top + 18, option.checked ? emerald : card, 5);
            if (!option.checked) GdiFrame(dc, 37, option.top, 55, option.top + 18, border, 5);
            else {
                HPEN pen = CreatePen(PS_SOLID, 2, onEmerald);
                HGDIOBJ oldPen = SelectObject(dc, pen);
                MoveToEx(dc, 41, option.top + 9, nullptr);
                LineTo(dc, 45, option.top + 13);
                LineTo(dc, 52, option.top + 5);
                SelectObject(dc, oldPen);
                DeleteObject(pen);
            }
            GdiText(dc, g_fontBody, ink, option.label, 67, option.top - 4, 400, option.top + 23, DT_LEFT);
        }

        GdiFill(dc, 28, 309, 532, 310, border, 0);
        GdiFill(dc, 356, 322, 524, 366, emerald, 10);
        GdiText(dc, g_fontBody, onEmerald, L"安装", 356, 322, 524, 366, DT_CENTER);
        return;
    }

    if (mode == kComplete) {
        GdiText(dc, g_fontTitle, ink, L"安装完成", 36, 106, 476, 148, DT_LEFT);
        GdiText(dc, g_fontBody, muted, L"ALEMBIC 已安装。", 36, 161, 476, 189, DT_LEFT);
        GdiFill(dc, 388, 322, 524, 366, emerald, 10);
        GdiText(dc, g_fontBody, onEmerald, L"完成", 388, 322, 524, 366, DT_CENTER);
        return;
    }

    const wchar_t* status = mode == kPreparing ? L"正在准备安装…" :
        mode == kFinalizing ? L"正在配置运行环境…" : L"正在安装 ALEMBIC…";
    GdiText(dc, g_fontTitle, ink, L"安装 ALEMBIC", 36, 94, 476, 129, DT_LEFT);
    GdiText(dc, g_fontBody, muted, status, 36, 166, 524, 194, DT_LEFT);

    const int barLeft = 36, barTop = 210, barWidth = 488, barHeight = 10;
    GdiFill(dc, barLeft, barTop, barLeft + barWidth, barTop + barHeight, border, barHeight);
    if (mode == kExtracting && g.progressKnown.load()) {
        const int percent = std::max(0, std::min(100, g.rawProgress.load()));
        if (percent > 0) {
            GdiFill(dc, barLeft, barTop, barLeft + barWidth * percent / 100, barTop + barHeight,
                emerald, barHeight);
        }
        wchar_t text[8]{};
        wsprintfW(text, L"%d%%", percent);
        GdiText(dc, g_fontSmall, muted, text, 36, 229, 524, 251, DT_RIGHT);
        return;
    }

    // Indeterminate sweep.
    const int segment = barWidth / 5;
    const int offset = animationOffset % (barWidth + segment) - segment;
    int left = barLeft + offset;
    int right = left + segment;
    if (left < barLeft) left = barLeft;
    if (right > barLeft + barWidth) right = barLeft + barWidth;
    if (right > left) GdiFill(dc, left, barTop, right, barTop + barHeight, emerald, barHeight);
    animationOffset = (animationOffset + 8) % (barWidth + segment);
}

// Temporary tracing: tells us exactly how far the UI thread gets.
void TraceUi(const char* message) {
    wchar_t path[MAX_PATH]{};
    if (GetTempPathW(MAX_PATH, path) == 0) return;
    lstrcatW(path, L"alembic-ui-trace.log");
    HANDLE file = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
        nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return;
    wchar_t line[256]{};
    MultiByteToWideChar(CP_ACP, 0, message, -1, line, 256);
    lstrcatW(line, L"\r\n");
    DWORD written = 0;
    WriteFile(file, line, static_cast<DWORD>(lstrlenW(line) * sizeof(wchar_t)), &written, nullptr);
    CloseHandle(file);
}

HBRUSH BackgroundBrush() {
    if (!g.backgroundBrush) {
        g.backgroundBrush = CreateSolidBrush(g.dark ? RGB(18, 23, 20) : RGB(250, 248, 241));
    }
    return g.backgroundBrush;
}

void PaintInstaller(HWND window) {
    static bool traced = false;
    if (!traced) { traced = true; TraceUi("paint"); }
    PAINTSTRUCT paint{};
    HDC hdc = BeginPaint(window, &paint);
    RECT client{};
    GetClientRect(window, &client);
    if (client.right <= 0 || client.bottom <= 0) {
        EndPaint(window, &paint);
        return;
    }
    RenderGdi(hdc);
    EndPaint(window, &paint);
}

LRESULT CALLBACK InstallerWindowProc(HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    switch (message) {
        case WM_CREATE: {
            if (g.mode.load() == kWelcome) {
                CreateDirectoryEdit(window);
                if (!g.directoryEdit.load()) return -1;
            }
            // A window region gives the frameless window real rounded corners on
            // every supported Windows version, not only where DWM rounding exists.
            HRGN region = CreateRoundRectRgn(0, 0, kWidth + 1, kHeight + 1, 24, 24);
            if (region && SetWindowRgn(window, region, TRUE) == 0) DeleteObject(region);
            SetTimer(window, 1, 80, nullptr);
            return 0;
        }
        case WM_ERASEBKGND:
            return 1;
        case WM_PAINT:
            PaintInstaller(window);
            return 0;
        case WM_CTLCOLORSTATIC:
        case WM_CTLCOLOREDIT: {
            HDC dc = reinterpret_cast<HDC>(wParam);
            const Palette p = CurrentPalette();
            SetTextColor(dc, g.dark ? RGB(230, 236, 225) : RGB(76, 85, 73));
            SetBkColor(dc, g.dark ? RGB(31, 40, 35) : RGB(255, 255, 252));
            return reinterpret_cast<LRESULT>(g.editBrush);
        }
        case WM_TIMER: {
            static bool tracedTimer = false;
            if (!tracedTimer) { tracedTimer = true; TraceUi("timer"); }
            if (!g.foregroundSet.exchange(true)) SetForegroundWindow(window);
            // NSIS re-shows its wizard when the page is displayed; keep it hidden
            // while the frameless window owns the wizard.
            const HWND parent = g.installer;
            if (parent && IsWindowVisible(parent)) ShowWindow(parent, SW_HIDE);

            const HWND edit = g.directoryEdit.load();
            if (edit) ShowWindow(edit, g.mode.load() == kWelcome ? SW_SHOW : SW_HIDE);

            // Only the progress screens animate; repainting the welcome page every
            // 80 ms would keep the UI thread busy for no visible gain.
            const int mode = g.mode.load();
            if (mode != kWelcome && mode != kComplete) InvalidateRect(window, nullptr, FALSE);
            return 0;
        }
        case kUiUpdate: {
            const HWND edit = g.directoryEdit.load();
            if (edit) ShowWindow(edit, g.mode.load() == kWelcome ? SW_SHOW : SW_HIDE);
            InvalidateRect(window, nullptr, FALSE);
            return 0;
        }
        case kUiShutdown:
            DestroyWindow(window);
            return 0;
        case WM_MOUSEMOVE: {
            TRACKMOUSEEVENT tracking{sizeof(tracking), TME_LEAVE, window, 0};
            TrackMouseEvent(&tracking);
            InvalidateRect(window, nullptr, FALSE);
            return 0;
        }
        case WM_MOUSELEAVE:
            InvalidateRect(window, nullptr, FALSE);
            return 0;
        case WM_LBUTTONDOWN: {
            POINT point{GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam)};
            if (point.y < 58 && !Inside(MinimizeButton(), point) && !Inside(CloseButton(), point)) {
                ReleaseCapture();
                SendMessageW(window, WM_NCLBUTTONDOWN, HTCAPTION, 0);
                return 0;
            }
            SetCapture(window);
            return 0;
        }
        case WM_LBUTTONUP: {
            ReleaseCapture();
            POINT point{GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam)};
            const int mode = g.mode.load();
            if (Inside(MinimizeButton(), point)) {
                ShowWindow(window, SW_MINIMIZE);
            } else if (Inside(CloseButton(), point)) {
                DispatchWizardCommand(IDCANCEL);
            } else if (mode == kWelcome && Inside(WelcomeBrowseButton(), point)) {
                ChooseFolder(window);
            } else if (mode == kWelcome && Inside(DesktopOption(), point)) {
                g.desktopShortcut.store(!g.desktopShortcut.load());
                InvalidateRect(window, nullptr, FALSE);
            } else if (mode == kWelcome && Inside(StartMenuOption(), point)) {
                g.startMenuShortcut.store(!g.startMenuShortcut.load());
                InvalidateRect(window, nullptr, FALSE);
            } else if (mode == kWelcome && Inside(WelcomeInstallButton(), point)) {
                PersistOptions();
                g.mode.store(kPreparing);
                InvalidateRect(window, nullptr, FALSE);
                DispatchWizardCommand(IDOK);
            } else if (mode == kComplete && Inside(FinishButton(), point)) {
                DispatchWizardCommand(IDOK);
            }
            return 0;
        }
        case WM_KEYDOWN:
            if (wParam == VK_ESCAPE) DispatchWizardCommand(IDCANCEL);
            else if (wParam == VK_RETURN && g.mode.load() == kWelcome) {
                PersistOptions();
                g.mode.store(kPreparing);
                DispatchWizardCommand(IDOK);
            }
            return 0;
        case WM_CLOSE:
            DispatchWizardCommand(g.mode.load() == kComplete ? IDOK : IDCANCEL);
            return 0;
        case WM_DESTROY:
            g.directoryEdit.store(nullptr);
            g.window.store(nullptr);
            KillTimer(window, 1);
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProcW(window, message, wParam, lParam);
}

DWORD WINAPI UiThreadProc(void*) {
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    g_fontTitle = MakeUiFont(22, true);
    g_fontBody = MakeUiFont(14, false);
    g_fontSmall = MakeUiFont(13, false);

    WNDCLASSEXW wc{};
    wc.cbSize = sizeof(wc);
    wc.style = CS_DROPSHADOW | CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = InstallerWindowProc;
    wc.hInstance = g.module;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hbrBackground = nullptr;
    wc.lpszClassName = kWindowClass;
    RegisterClassExW(&wc);

    const int x = (GetSystemMetrics(SM_CXSCREEN) - kWidth) / 2;
    const int y = (GetSystemMetrics(SM_CYSCREEN) - kHeight) / 2;
    HWND window = CreateWindowExW(WS_EX_APPWINDOW, kWindowClass, L"ALEMBIC Setup",
        WS_POPUP | WS_MINIMIZEBOX, x, y, kWidth, kHeight, nullptr, nullptr, g.module, nullptr);
    g.window = window;
    TraceUi(window ? "window-created" : "window-failed");
    if (!window) {
        if (g.readyEvent) SetEvent(g.readyEvent);
        DestroyUiFonts();
        CoUninitialize();
        return 1;
    }
    if (g.cancelled.load()) {
        // The caller already gave up waiting; do not show a window nobody owns.
        DestroyWindow(window);
        g.window.store(nullptr);
        if (g.readyEvent) SetEvent(g.readyEvent);
        DestroyUiFonts();
        CoUninitialize();
        return 1;
    }
    // Show the window and hand control straight to the message loop: nothing
    // between here and GetMessage may block, or the window stays unpainted with
    // a busy cursor. DWM attributes and forced z-order changes are deliberately
    // not used here.
    ShowWindow(window, SW_SHOWNORMAL);
    if (g.readyEvent) SetEvent(g.readyEvent);
    InvalidateRect(window, nullptr, TRUE);
    TraceUi("shown");

    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        // Only a focused edit control needs dialog navigation; running it for the
        // whole window swallows mouse and keyboard messages.
        if (g.directoryEdit.load() && GetFocus() == g.directoryEdit.load() &&
            IsDialogMessageW(window, &message)) {
            continue;
        }
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }

    if (g.directoryEdit) DestroyWindow(g.directoryEdit);
    if (g.editFont) DeleteObject(g.editFont);
    if (g.editBrush) DeleteObject(g.editBrush);
    if (g.backgroundBrush) {
        DeleteObject(g.backgroundBrush);
        g.backgroundBrush = nullptr;
    }
    DestroyUiFonts();
    CoUninitialize();
    return 0;
}

LRESULT CALLBACK ProgressHook(int code, WPARAM wParam, LPARAM lParam) {
    if (code >= 0 && lParam) {
        const auto* call = reinterpret_cast<const CWPSTRUCT*>(lParam);
        wchar_t className[64]{};
        if (call->hwnd && GetClassNameW(call->hwnd, className, static_cast<int>(std::size(className))) &&
            lstrcmpiW(className, PROGRESS_CLASSW) == 0) {
            switch (call->message) {
                case kPbmSetRange: {
                    const LPARAM range = call->lParam;
                    g.progressMinimum.store(static_cast<short>(LOWORD(range)));
                    g.progressMaximum.store(static_cast<short>(HIWORD(range)));
                    break;
                }
                case kPbmSetRange32:
                    g.progressMinimum.store(static_cast<int>(call->wParam));
                    g.progressMaximum.store(static_cast<int>(call->lParam));
                    break;
                case kPbmSetPos: {
                    const int minimum = g.progressMinimum.load();
                    const int maximum = g.progressMaximum.load();
                    const int span = std::max(1, maximum - minimum);
                    const int position = static_cast<int>(call->wParam);
                    const int percent = std::max(0, std::min(100, ((position - minimum) * 100) / span));
                    g.rawProgress.store(percent);
                    // NSIS drives this bar per section; a single 0 -> 100 jump is not a
                    // usable progress reading, so keep the honest indeterminate animation.
                    if (percent > 0 && percent < 100) g.progressKnown.store(true);
                    if (g.window.load()) PostMessageW(g.window.load(), kUiUpdate, 0, 0);
                    break;
                }
                case kPbmDeltaPos: {
                    const int minimum = g.progressMinimum.load();
                    const int maximum = g.progressMaximum.load();
                    const int span = std::max(1, maximum - minimum);
                    const int delta = static_cast<int>(call->wParam);
                    const int percent = std::max(0, std::min(100, g.rawProgress.load() + (delta * 100) / span));
                    g.rawProgress.store(percent);
                    if (percent > 0 && percent < 100) g.progressKnown.store(true);
                    if (g.window.load()) PostMessageW(g.window.load(), kUiUpdate, 0, 0);
                    break;
                }
                case kPbmSetStep:
                    g.progressStep.store(static_cast<int>(call->wParam));
                    break;
                case kPbmStepIt: {
                    const int minimum = g.progressMinimum.load();
                    const int maximum = g.progressMaximum.load();
                    const int span = std::max(1, maximum - minimum);
                    const int percent = std::max(0, std::min(100, g.rawProgress.load() +
                        (g.progressStep.load() * 100) / span));
                    g.rawProgress.store(percent);
                    if (percent > 0 && percent < 100) g.progressKnown.store(true);
                    if (g.window.load()) PostMessageW(g.window.load(), kUiUpdate, 0, 0);
                    break;
                }
            }
        }
    }
    return CallNextHookEx(g.progressHook, code, wParam, lParam);
}

} // namespace

namespace {

// The options INI lives next to this plug-in inside $PLUGINSDIR, so NSIS only has
// to pass the installation directory.
std::wstring ModuleDirectory() {
    wchar_t path[MAX_PATH]{};
    const DWORD length = GetModuleFileNameW(g.module, path, MAX_PATH);
    if (length == 0 || length >= MAX_PATH) return std::wstring();
    std::wstring full(path, length);
    const std::wstring::size_type slash = full.find_last_of(L"\\/");
    if (slash == std::wstring::npos) return std::wstring();
    return full.substr(0, slash);
}

// NSIS shows its own wizard again when the page is displayed. This short watchdog
// keeps it hidden and, unlike the UI-thread timer, it works even if the first
// paint is slow.
DWORD WINAPI HideParentProc(void* parameter) {
    HWND parent = static_cast<HWND>(parameter);
    for (int attempt = 0; attempt < 100; ++attempt) {
        if (!IsWindow(parent)) break;
        if (IsWindowVisible(parent)) ShowWindow(parent, SW_HIDE);
        Sleep(100);
    }
    return 0;
}

void KeepParentHidden(HWND parent) {
    if (!parent) return;
    ShowWindow(parent, SW_HIDE);
    HANDLE watchdog = CreateThread(nullptr, 0, HideParentProc, parent, 0, nullptr);
    if (watchdog) CloseHandle(watchdog);
}

int CreateUi(HWND installer, const std::wstring& installDirectory) {
    if (!installer) return 0;

    // Idempotent: NSIS may create the same page more than once (for example when
    // the wizard is re-shown). A second call must re-activate the existing window
    // instead of reporting a failure that aborts the installation.
    const HWND existing = g.window.load();
    if (g.uiThread && existing) {
        ShowWindow(installer, SW_HIDE);
        ShowWindow(existing, SW_SHOWNORMAL);
        SetForegroundWindow(existing);
        return 1;
    }
    if (g.uiThread) return 0;

    KeepParentHidden(installer);

    g.installer = installer;
    g.initialDirectory = installDirectory;
    g.optionsPath = ModuleDirectory();
    if (!g.optionsPath.empty()) {
        g.optionsPath += L"\\alembic-ui.ini";
        DeleteFileW(g.optionsPath.c_str());
    }
    g.dark = ReadDarkMode();
    g.cancelled.store(false);
    g.readyEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!g.readyEvent) return 0;

    g.uiThread = CreateThread(nullptr, 0, UiThreadProc, nullptr, 0, &g.uiThreadId);
    if (!g.uiThread) {
        CloseHandle(g.readyEvent);
        g.readyEvent = nullptr;
        return 0;
    }

    // Do not give up too early, and never tear down a window that already exists:
    // the readiness event and window creation can be a few hundred milliseconds
    // apart on a cold start.
    WaitForSingleObject(g.readyEvent, kStartupTimeoutMs);
    CloseHandle(g.readyEvent);
    g.readyEvent = nullptr;

    if (!g.window) {
        // Stop a late window from appearing after we already reported failure.
        g.cancelled.store(true);
        // The window thread already exited; release its handle so a retry can start clean.
        if (g.uiThread) {
            if (WaitForSingleObject(g.uiThread, 5000) == WAIT_OBJECT_0) {
                CloseHandle(g.uiThread);
                g.uiThread = nullptr;
            }
        }
        return 0;
    }

    // Installed only once the window exists, so a failed creation leaves no hook.
    g.progressHook = SetWindowsHookExW(WH_CALLWNDPROC, ProgressHook, g.module, GetCurrentThreadId());
    return 1;
}

void ApplyMode(int mode) {
    if (mode < kWelcome || mode > kComplete) return;
    if (mode == kExtracting) {
        g.rawProgress.store(0);
        g.progressKnown.store(false);
    }
    if (mode == kComplete) g.rawProgress.store(100);
    g.mode.store(mode);
    if (g.window) PostMessageW(g.window, kUiUpdate, 0, 0);
}

void ShutdownUi() {
    if (g.progressHook) {
        UnhookWindowsHookEx(g.progressHook);
        g.progressHook = nullptr;
    }
    if (g.window) PostMessageW(g.window, kUiShutdown, 0, 0);
    if (g.uiThread) {
        // The UI thread may own a modal folder picker; never block the wizard forever.
        if (WaitForSingleObject(g.uiThread, 10000) == WAIT_OBJECT_0) {
            CloseHandle(g.uiThread);
            g.uiThread = nullptr;
        }
    }
}

// --- NSIS plug-in ABI -------------------------------------------------------
// NSIS calls the exported entries directly (alembic-installer-ui::AlembicCreate),
// so no argument marshalling library is involved. Only the tiny subset of the
// documented exdll.h ABI that is actually used here is reproduced.

int g_stringSize = 0;
stack_t** g_stackTop = nullptr;

void PopArg(std::wstring& value) {
    value.clear();
    if (!g_stackTop || !*g_stackTop) return;
    stack_t* node = *g_stackTop;
    value.assign(node->text);
    *g_stackTop = node->next;
    GlobalFree(node);
}

void PushInt(int value) {
    if (!g_stackTop) return;
    stack_t* node = static_cast<stack_t*>(
        GlobalAlloc(GPTR, sizeof(stack_t) + static_cast<SIZE_T>(g_stringSize) * sizeof(wchar_t)));
    if (!node) return;
    swprintf_s(node->text, static_cast<size_t>(g_stringSize), L"%d", value);
    node->next = *g_stackTop;
    *g_stackTop = node;
}

} // namespace

extern "C" void __cdecl AlembicCreate(HWND hwndParent, int stringSize, wchar_t*,
                                      stack_t** stackTop, void*) {
    g_stringSize = stringSize;
    g_stackTop = stackTop;
    std::wstring directory;
    PopArg(directory);
    PushInt(CreateUi(hwndParent, directory));
}

extern "C" void __cdecl AlembicSetMode(HWND, int stringSize, wchar_t*,
                                       stack_t** stackTop, void*) {
    g_stringSize = stringSize;
    g_stackTop = stackTop;
    std::wstring mode;
    PopArg(mode);
    ApplyMode(static_cast<int>(wcstol(mode.c_str(), nullptr, 10)));
}

extern "C" void __cdecl AlembicShutdown(HWND, int stringSize, wchar_t*,
                                        stack_t** stackTop, void*) {
    g_stringSize = stringSize;
    g_stackTop = stackTop;
    ShutdownUi();
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g.module = instance;
        DisableThreadLibraryCalls(instance);
    }
    return TRUE;
}
