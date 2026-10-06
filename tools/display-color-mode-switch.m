#import <Foundation/Foundation.h>
#import <QuartzCore/QuartzCore.h>
#import <objc/message.h>
#include <poll.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static id readValue(id object, NSString *key) {
    if (!object || ![object respondsToSelector:NSSelectorFromString(key)]) return nil;
    @try { return [object valueForKey:key]; }
    @catch (__unused NSException *exception) { return nil; }
}

static NSString *modeID(id mode) {
    id value = readValue(mode, @"internalRepresentation");
    return [value respondsToSelector:@selector(stringValue)] ? [value stringValue] : nil;
}

static id displayWithID(NSArray *displays, uint32_t displayID) {
    for (id display in displays) {
        id value = readValue(display, @"displayId");
        if ([value respondsToSelector:@selector(unsignedIntValue)] && [value unsignedIntValue] == displayID)
            return display;
    }
    return nil;
}

static id availableMode(id display, NSString *targetID) {
    id modes = readValue(display, @"availableModes");
    if (![modes isKindOfClass:NSArray.class]) return nil;
    for (id mode in modes)
        if ([modeID(mode) isEqualToString:targetID]) return mode;
    return nil;
}

static void setCurrentMode(id display, id mode) {
    SEL selector = NSSelectorFromString(@"setCurrentMode:");
    ((void (*)(id, SEL, id))objc_msgSend)(display, selector, mode);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 3) {
            fprintf(stderr, "Expected displayID and an available CADisplayMode identifier.\n");
            return 2;
        }
        char *end = NULL;
        uint32_t displayID = (uint32_t)strtoul(argv[1], &end, 10);
        if (!end || *end) return 2;
        NSString *targetID = [NSString stringWithUTF8String:argv[2]];
        Class displayClass = NSClassFromString(@"CADisplay");
        SEL displaysSelector = NSSelectorFromString(@"displays");
        if (!displayClass || ![displayClass respondsToSelector:displaysSelector] ||
            ![displayClass instancesRespondToSelector:NSSelectorFromString(@"setCurrentMode:")]) {
            puts("ERROR: This macOS version does not expose the required CADisplay mode API.");
            return 3;
        }
        NSArray *displays = ((id (*)(id, SEL))objc_msgSend)(displayClass, displaysSelector);
        id display = displayWithID(displays, displayID);
        if (!display) {
            puts("ERROR: The display is no longer available. Refresh the display list and try again.");
            return 3;
        }
        if (![readValue(display, @"isExternal") boolValue]) {
            puts("ERROR: Color mode switching is limited to external displays.");
            return 3;
        }
        // Do not gate this on CGDisplayIsActive/CGDisplayIsOnline. CoreGraphics
        // can report mirror members as inactive, while CADisplay still exposes
        // their current mode and driver-supported color modes.
        id original = readValue(display, @"currentMode");
        id target = availableMode(display, targetID);
        if (!original || !target) {
            puts("ERROR: The exact driver-reported color mode is no longer available. Refresh and try again.");
            return 4;
        }
        double originalRate = [readValue(original, @"refreshRate") doubleValue];
        double targetRate = [readValue(target, @"refreshRate") doubleValue];
        if ([readValue(original, @"width") integerValue] != [readValue(target, @"width") integerValue] ||
            [readValue(original, @"height") integerValue] != [readValue(target, @"height") integerValue] ||
            fabs(originalRate - targetRate) > 0.01) {
            puts("ERROR: Refusing to change color mode because the selected mode changes resolution or refresh rate.");
            return 4;
        }
        if ([modeID(original) isEqualToString:targetID]) {
            puts("ERROR: That color mode is already active.");
            return 4;
        }

        @try { setCurrentMode(display, target); }
        @catch (NSException *exception) {
            fprintf(stdout, "ERROR: macOS rejected the color mode preview (%s).\n", exception.reason.UTF8String ?: "unknown error");
            return 5;
        }
        id applied = readValue(display, @"currentMode");
        if (![modeID(applied) isEqualToString:targetID]) {
            @try { setCurrentMode(display, original); } @catch (__unused NSException *exception) {}
            puts("ERROR: macOS did not report the selected mode as active; the original mode was restored.");
            return 5;
        }
        puts("PREVIEW_APPLIED");
        fflush(stdout);

        struct pollfd input = { .fd = STDIN_FILENO, .events = POLLIN | POLLHUP };
        int ready = poll(&input, 1, 25000);
        char response[32] = {0};
        ssize_t length = ready > 0 ? read(STDIN_FILENO, response, sizeof(response) - 1) : 0;
        BOOL keep = length > 0 && strncmp(response, "keep", 4) == 0;
        if (keep) {
            puts("KEPT_SESSION_ONLY");
            fflush(stdout);
            return 0;
        }
        @try { setCurrentMode(display, original); }
        @catch (NSException *exception) {
            fprintf(stdout, "ERROR: Automatic restore failed (%s). Use System Settings > Displays to restore a mode.\n",
                    exception.reason.UTF8String ?: "unknown error");
            return 8;
        }
        if ([modeID(readValue(display, @"currentMode")) isEqualToString:modeID(original)]) {
            puts("REVERTED");
            fflush(stdout);
            return 0;
        }
        puts("ERROR: Automatic restore did not return to the original mode. Use System Settings > Displays to restore it.");
        return 8;
    }
}
