#import <AppKit/AppKit.h>
#include <stdbool.h>
#include <stdint.h>

typedef bool (*DMIShortcutCallback)(uint16_t keyCode, bool isKeyDown,
                                    bool commandOnly, bool commandOption,
                                    bool shiftOnly);

static id dmiMonitor = nil;
static DMIShortcutCallback dmiCallback = NULL;

bool DMIInstallShortcutMonitor(DMIShortcutCallback callback) {
    if (callback == NULL) return false;
    if (dmiMonitor != nil) return false;
    dmiCallback = callback;
    dmiMonitor = [NSEvent addLocalMonitorForEventsMatchingMask:
                  (NSEventMaskKeyDown | NSEventMaskKeyUp)
                                                     handler:^NSEvent *(NSEvent *event) {
        NSEventModifierFlags modifiers = event.modifierFlags & NSEventModifierFlagDeviceIndependentFlagsMask;
        if (dmiCallback == NULL) return event;
        uint16_t code = event.keyCode;
        if (event.type == NSEventTypeKeyUp) {
            if (code != 34) return event;
            return dmiCallback(code, false, false, false, false) ? nil : event;
        }
        bool arrowKey = code >= 123 && code <= 126;
        if ((event.isARepeat && !arrowKey) ||
            ((modifiers & NSEventModifierFlagControl) != 0) ||
            ((modifiers & NSEventModifierFlagShift) != 0 && !arrowKey))
            return event;
        bool shiftOnly = (modifiers & NSEventModifierFlagShift) != 0;
        bool hasCommand = (modifiers & NSEventModifierFlagCommand) != 0;
        bool hasOption = (modifiers & NSEventModifierFlagOption) != 0;
        bool commandOption = hasCommand && hasOption;
        if (hasOption && !commandOption) return event;
        bool commandOnly = hasCommand && !hasOption;
        if (commandOption) {
            if (code != 3) return event;
            return dmiCallback(code, true, false, true, false) ? nil : event;
        }
        if (commandOnly) {
            if (code != 0 && code != 1 && code != 8 && code != 9 &&
                code != 13 && code != 14 && code != 44) return event;
            // ⌘A / ⌘S / ⌘C / ⌘V / ⌘W / ⌘E / ⌘/
        } else if (code != 0 && code != 3 && code != 8 && code != 15 &&
                   code != 34 && code != 46 && !arrowKey) {
            return event;
        }
        return dmiCallback(code, true, commandOnly, false, shiftOnly) ? nil : event;
    }];
    if (dmiMonitor == nil) dmiCallback = NULL;
    return dmiMonitor != nil;
}

void DMIRemoveShortcutMonitor(void) {
    if (dmiMonitor != nil) {
        [NSEvent removeMonitor:dmiMonitor];
        dmiMonitor = nil;
    }
    dmiCallback = NULL;
}
