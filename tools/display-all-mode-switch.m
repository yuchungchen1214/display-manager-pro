#import <Foundation/Foundation.h>
#import <QuartzCore/QuartzCore.h>
#import <CoreGraphics/CoreGraphics.h>
#import <objc/message.h>
#include <poll.h>
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

static NSDictionary *mirrorTopology(NSArray *displays) {
    NSMutableDictionary *topology = NSMutableDictionary.new;
    for (id display in displays) {
        uint32_t displayID = [readValue(display, @"displayId") unsignedIntValue];
        topology[@(displayID).stringValue] = @(CGDisplayMirrorsDisplay(displayID));
    }
    return topology;
}

static BOOL receiverMayChangeOnlyRefresh(id original, id target) {
    // CADisplayMode's mode identifier includes timing. A mirror receiver is
    // allowed to change that timing only; all other reported image properties
    // must remain identical.
    for (NSString *key in @[@"width", @"height", @"colorMode", @"bitDepth", @"hdrMode",
                            @"isVRR", @"colorGamut", @"pixelAspectRatio", @"preferredScale"]) {
        id before = readValue(original, key);
        id after = readValue(target, key);
        if (before == nil || after == nil) {
            if (before != after) return NO;
        } else if (![before isEqual:after]) {
            return NO;
        }
    }
    return YES;
}

static void setCurrentMode(id display, id mode) {
    SEL selector = NSSelectorFromString(@"setCurrentMode:");
    ((void (*)(id, SEL, id))objc_msgSend)(display, selector, mode);
}

static BOOL restoreMode(id display, id original) {
    @try { setCurrentMode(display, original); }
    @catch (__unused NSException *exception) { return NO; }
    return [modeID(readValue(display, @"currentMode")) isEqualToString:modeID(original)];
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
        id original = readValue(display, @"currentMode");
        id target = availableMode(display, targetID);
        if (!display || !original || !target) {
            puts("ERROR: The exact driver-reported mode is no longer available. Refresh and try again.");
            return 3;
        }
        if ([modeID(original) isEqualToString:targetID]) {
            puts("ERROR: That display mode is already active.");
            return 4;
        }

        uint32_t mirrorTarget = CGDisplayMirrorsDisplay(displayID);
        BOOL isReceiver = mirrorTarget != kCGNullDirectDisplay && mirrorTarget != displayID;
        if (isReceiver && !receiverMayChangeOnlyRefresh(original, target)) {
            puts("ERROR: Only refresh rate can be changed while mirrored.");
            return 4;
        }
        NSDictionary *topologyBefore = mirrorTopology(displays);
        @try { setCurrentMode(display, target); }
        @catch (NSException *exception) {
            fprintf(stdout, "ERROR: macOS rejected the mode preview (%s).\n",
                    exception.reason.UTF8String ?: "unknown error");
            return 5;
        }
        id applied = readValue(display, @"currentMode");
        NSDictionary *topologyAfter = mirrorTopology(displays);
        if (![modeID(applied) isEqualToString:targetID] || ![topologyBefore isEqual:topologyAfter]) {
            BOOL restored = restoreMode(display, original);
            if (![topologyBefore isEqual:topologyAfter])
                puts("ERROR: The mode change would alter the mirror arrangement; the original mode was restored.");
            else if (restored)
                puts("ERROR: macOS did not report the selected mode as active; the original mode was restored.");
            else
                puts("ERROR: The selected mode was not confirmed and automatic restore failed. Check Displays settings.");
            return 5;
        }
        puts("PREVIEW_APPLIED");
        fflush(stdout);

        struct pollfd input = { .fd = STDIN_FILENO, .events = POLLIN | POLLHUP };
        int ready = poll(&input, 1, 25000);
        char response[32] = {0};
        ssize_t length = ready > 0 ? read(STDIN_FILENO, response, sizeof(response) - 1) : 0;
        if (length > 0 && strncmp(response, "keep", 4) == 0) {
            puts("KEPT_SESSION_ONLY");
            fflush(stdout);
            return 0;
        }
        if (restoreMode(display, original)) {
            puts("REVERTED");
            fflush(stdout);
            return 0;
        }
        puts("ERROR: Automatic restore failed. Use System Settings > Displays to restore a mode.");
        return 8;
    }
}
