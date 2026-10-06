#import <AppKit/AppKit.h>
#include <stdbool.h>
#include <stdint.h>

extern bool DMIInstallShortcutMonitor(bool (*callback)(uint16_t, bool, bool));
extern void DMIRemoveShortcutMonitor(void);

static int downSeen = 0;
static int upSeen = 0;
static int commandSeen = 0;
static int exportSeen = 0;
static int helpSeen = 0;

static bool handleKey(uint16_t keyCode, bool isKeyDown, bool commandOnly) {
    if (keyCode == 34) {
        if (isKeyDown) ++downSeen;
        else ++upSeen;
    }
    if (keyCode == 1 && isKeyDown && commandOnly) ++commandSeen;
    if (keyCode == 14 && isKeyDown && commandOnly) ++exportSeen;
    if (keyCode == 44 && isKeyDown && commandOnly) ++helpSeen;
    return true;
}

static void sendKey(NSEventType type, uint16_t keyCode, NSEventModifierFlags modifiers) {
    NSEvent *event = [NSEvent keyEventWithType:type
                                      location:NSZeroPoint
                                 modifierFlags:modifiers
                                     timestamp:0
                                  windowNumber:0
                                       context:nil
                                    characters:@"i"
                   charactersIgnoringModifiers:@"i"
                                     isARepeat:NO
                                       keyCode:keyCode];
    [NSApp sendEvent:event];
}

int main(void) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        if (!DMIInstallShortcutMonitor(handleKey)) return 1;
        sendKey(NSEventTypeKeyDown, 34, 0);
        sendKey(NSEventTypeKeyUp, 34, 0);
        sendKey(NSEventTypeKeyDown, 1, NSEventModifierFlagCommand);
        sendKey(NSEventTypeKeyDown, 14, NSEventModifierFlagCommand);
        sendKey(NSEventTypeKeyDown, 44, NSEventModifierFlagCommand);
        DMIRemoveShortcutMonitor();
        return (downSeen == 1 && upSeen == 1 && commandSeen == 1 &&
                exportSeen == 1 && helpSeen == 1) ? 0 : 2;
    }
}
