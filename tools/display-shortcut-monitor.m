#import <AppKit/AppKit.h>
#include <stdbool.h>
#include <stdint.h>

typedef bool (*DMIShortcutCallback)(uint16_t keyCode, bool isKeyDown, bool commandOnly);

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
            return dmiCallback(code, false, false) ? nil : event;
        }
        if (event.isARepeat ||
            (modifiers & (NSEventModifierFlagShift | NSEventModifierFlagControl |
                          NSEventModifierFlagOption)) != 0) return event;
        bool commandOnly = (modifiers & NSEventModifierFlagCommand) != 0;
        if (commandOnly) {
            if (code != 1 && code != 13 && code != 14 && code != 44) return event;
            // ⌘S / ⌘W / ⌘E / ⌘/
        } else if (code != 0 && code != 3 && code != 8 && code != 15 && code != 34) {
            return event;
        }
        return dmiCallback(code, true, commandOnly) ? nil : event;
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
