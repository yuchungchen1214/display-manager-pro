#import <AppKit/AppKit.h>
#include <unistd.h>

@interface IdentifyOutlineView : NSView
@property(nonatomic, copy) NSArray<NSDictionary *> *members;
@property(nonatomic, copy) NSDictionary<NSString *, NSString *> *mappingSettings;
@property(nonatomic) BOOL identifyVisible;
@property(nonatomic, strong) NSColor *identifyColor;
@end

@implementation IdentifyOutlineView
- (void)drawRect:(NSRect)dirtyRect {
    [super drawRect:dirtyRect];
    NSString *enabled = self.mappingSettings[@"enabled"] ?: @"off";
    NSString *backgroundName = self.mappingSettings[@"background"] ?: @"black";
    NSString *lineName = self.mappingSettings[@"line"] ?: @"white";
    BOOL mappingEnabled = ![enabled isEqualToString:@"off"];
    if (mappingEnabled) {
        CGFloat alpha = [enabled isEqualToString:@"translucent"] ? 0.5 : 1.0;
        NSColor *background = [backgroundName isEqualToString:@"white"]
            ? NSColor.whiteColor : NSColor.blackColor;
        NSColor *lineColor = NSColor.whiteColor;
        if ([lineName isEqualToString:@"black"]) lineColor = NSColor.blackColor;
        else if ([lineName isEqualToString:@"red"]) lineColor = NSColor.redColor;
        else if ([lineName isEqualToString:@"blue"]) lineColor = NSColor.blueColor;
        else if ([lineName isEqualToString:@"green"]) lineColor = NSColor.greenColor;
        NSColor *minorLine = [lineColor colorWithAlphaComponent:alpha * 0.55];
        NSColor *majorLine = [lineColor colorWithAlphaComponent:alpha];
        background = [background colorWithAlphaComponent:alpha];
        [background setFill];
        NSRectFill(self.bounds);

        const CGFloat gridSpacing = 50.0;
        const CGFloat centerX = NSWidth(self.bounds) / 2.0;
        const CGFloat centerY = NSHeight(self.bounds) / 2.0;
        for (CGFloat x = centerX; x >= 0; x -= gridSpacing) {
            BOOL center = fabs(x - centerX) < 0.01;
            [(center ? majorLine : minorLine) setStroke];
            NSBezierPath *line = [NSBezierPath bezierPath];
            line.lineWidth = center ? 3.0 : 0.7;
            [line moveToPoint:NSMakePoint(x, 0)];
            [line lineToPoint:NSMakePoint(x, NSHeight(self.bounds))];
            [line stroke];
        }
        for (CGFloat x = centerX + gridSpacing; x <= NSWidth(self.bounds); x += gridSpacing) {
            [minorLine setStroke];
            NSBezierPath *line = [NSBezierPath bezierPath];
            line.lineWidth = 0.7;
            [line moveToPoint:NSMakePoint(x, 0)];
            [line lineToPoint:NSMakePoint(x, NSHeight(self.bounds))];
            [line stroke];
        }
        for (CGFloat y = centerY; y >= 0; y -= gridSpacing) {
            BOOL center = fabs(y - centerY) < 0.01;
            [(center ? majorLine : minorLine) setStroke];
            NSBezierPath *line = [NSBezierPath bezierPath];
            line.lineWidth = center ? 3.0 : 0.7;
            [line moveToPoint:NSMakePoint(0, y)];
            [line lineToPoint:NSMakePoint(NSWidth(self.bounds), y)];
            [line stroke];
        }
        for (CGFloat y = centerY + gridSpacing; y <= NSHeight(self.bounds); y += gridSpacing) {
            [minorLine setStroke];
            NSBezierPath *line = [NSBezierPath bezierPath];
            line.lineWidth = 0.7;
            [line moveToPoint:NSMakePoint(0, y)];
            [line lineToPoint:NSMakePoint(NSWidth(self.bounds), y)];
            [line stroke];
        }

        [minorLine setStroke];
        NSBezierPath *border = [NSBezierPath bezierPathWithRect:
            NSInsetRect(self.bounds, 0.35, 0.35)];
        border.lineWidth = 0.7;
        [border stroke];
    }

    if (!self.identifyVisible) return;
    // Keep the visible stroke flush with the screen edges. NSBezierPath centers
    // its stroke on the path, so inset by half the width to avoid clipping it.
    NSBezierPath *border = [NSBezierPath bezierPathWithRect:NSInsetRect(self.bounds, 5, 5)];
    border.lineWidth = 10;
    [[self.identifyColor colorWithAlphaComponent:0.85] setStroke];
    [border stroke];

    NSDictionary *nameAttributes = @{
        NSFontAttributeName: [NSFont boldSystemFontOfSize:21],
        NSForegroundColorAttributeName: NSColor.blackColor,
    };
    NSArray<NSDictionary *> *membersToShow = self.members.count > 1
        ? @[self.members.firstObject] : self.members;
    NSMutableArray<NSDictionary *> *lines = NSMutableArray.array;
    CGFloat contentWidth = 0;
    CGFloat contentHeight = 0;
    for (NSDictionary *member in membersToShow) {
        NSString *name = [member[@"name"] isKindOfClass:NSString.class]
            ? member[@"name"] : @"Display";
        NSString *friendly = [member[@"friendlyName"] isKindOfClass:NSString.class]
            ? member[@"friendlyName"] : @"";
        NSString *label = friendly.length ? friendly : name;
        NSSize size = [label sizeWithAttributes:nameAttributes];
        contentWidth = MAX(contentWidth, size.width);
        contentHeight += size.height;
        [lines addObject:@{@"text": label}];
    }
    if (!lines.count) return;
    CGFloat horizontalPadding = 14;
    CGFloat verticalPadding = 10;
    CGFloat topMargin = 34;
    CGFloat pillWidth = contentWidth + horizontalPadding * 2;
    CGFloat pillHeight = contentHeight + verticalPadding * 2;
    NSRect pill = NSMakeRect((NSWidth(self.bounds) - pillWidth) / 2,
                             NSMaxY(self.bounds) - topMargin - pillHeight,
                             pillWidth, pillHeight);
    [[self.identifyColor colorWithAlphaComponent:0.85] setFill];
    [[NSBezierPath bezierPathWithRoundedRect:pill xRadius:10 yRadius:10] fill];
    CGFloat y = NSMaxY(pill) - verticalPadding;
    for (NSDictionary *line in lines) {
        NSString *text = line[@"text"];
        NSSize size = [text sizeWithAttributes:nameAttributes];
        y -= size.height;
        CGFloat textX = NSMinX(pill) + (NSWidth(pill) - size.width) / 2;
        [text drawAtPoint:NSMakePoint(textX, y) withAttributes:nameAttributes];
    }
}
@end

static NSString *displayIDForScreen(NSScreen *screen) {
    NSNumber *number = screen.deviceDescription[@"NSScreenNumber"];
    return number ? number.stringValue : nil;
}

@interface IdentifyOverlayDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, copy) NSArray<NSDictionary *> *groups;
@property(nonatomic, strong) NSMutableDictionary<NSString *, NSPanel *> *panels;
@property(nonatomic, strong) NSMutableData *inputBuffer;
@property(nonatomic) BOOL visible;
@property(nonatomic, copy, nullable) NSSet<NSString *> *targetDisplayIDs;
@property(nonatomic, strong) NSMutableDictionary<NSString *, NSDictionary<NSString *, NSString *> *> *mappingStyles;
@property(nonatomic, strong) NSColor *identifyColor;
@end

@implementation IdentifyOverlayDelegate
- (instancetype)init {
    self = [super init];
    if (self) {
        _groups = @[];
        _panels = NSMutableDictionary.dictionary;
        _inputBuffer = NSMutableData.data;
        _mappingStyles = NSMutableDictionary.dictionary;
    }
    return self;
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSNotificationCenter.defaultCenter addObserver:self
        selector:@selector(displayParametersChanged:)
        name:NSApplicationDidChangeScreenParametersNotification object:nil];
    [self beginReadingCommands];
    static const char readyMessage[] = "{\"event\":\"ready\"}\n";
    write(STDOUT_FILENO, readyMessage, sizeof(readyMessage) - 1);
}

- (void)beginReadingCommands {
    NSFileHandle *input = NSFileHandle.fileHandleWithStandardInput;
    __weak typeof(self) weakSelf = self;
    input.readabilityHandler = ^(NSFileHandle *handle) {
        NSData *data = handle.availableData;
        if (data.length == 0) {
            handle.readabilityHandler = nil;
            dispatch_async(dispatch_get_main_queue(), ^{
                weakSelf.visible = NO;
                [weakSelf orderPanelsOut];
                [NSApp terminate:nil];
            });
            return;
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            [weakSelf consumeCommandData:data];
        });
    };
}

- (void)consumeCommandData:(NSData *)data {
    [self.inputBuffer appendData:data];
    const uint8_t *bytes = self.inputBuffer.bytes;
    NSUInteger start = 0;
    for (NSUInteger i = 0; i < self.inputBuffer.length; i++) {
        if (bytes[i] != '\n') continue;
        NSData *line = [self.inputBuffer subdataWithRange:NSMakeRange(start, i - start)];
        start = i + 1;
        if (!line.length) continue;
        NSError *error = nil;
        id value = [NSJSONSerialization JSONObjectWithData:line options:0 error:&error];
        if ([value isKindOfClass:NSDictionary.class]) [self handleCommand:value];
    }
    if (start) [self.inputBuffer replaceBytesInRange:NSMakeRange(0, start) withBytes:NULL length:0];
}

- (void)handleCommand:(NSDictionary *)command {
    NSString *action = [command[@"action"] isKindOfClass:NSString.class] ? command[@"action"] : @"";
    if ([action isEqualToString:@"configure"]) {
        NSArray *groups = [command[@"groups"] isKindOfClass:NSArray.class] ? command[@"groups"] : @[];
        [self setIdentifyColorFromHex:command[@"identifyColor"]];
        self.groups = groups;
        [self synchronizePanels];
    } else if ([action isEqualToString:@"accent"]) {
        [self setIdentifyColorFromHex:command[@"color"]];
        [self synchronizePanels];
    } else if ([action isEqualToString:@"show"]) {
        self.visible = YES;
        self.targetDisplayIDs = nil;
        [self synchronizePanels];
    } else if ([action isEqualToString:@"identify"]) {
        NSString *displayID = [command[@"displayID"] isKindOfClass:NSString.class]
            ? command[@"displayID"] : @"";
        self.visible = displayID.length > 0;
        self.targetDisplayIDs = displayID.length ? [NSSet setWithObject:displayID] : nil;
        [self synchronizePanels];
    } else if ([action isEqualToString:@"hide"]) {
        self.visible = NO;
        self.targetDisplayIDs = nil;
        [self synchronizePanels];
    } else if ([action isEqualToString:@"mapping"]) {
        NSString *displayID = [command[@"displayID"] isKindOfClass:NSString.class]
            ? command[@"displayID"] : @"";
        NSString *style = [command[@"enabled"] isKindOfClass:NSString.class]
            ? command[@"enabled"] : @"off";
        NSString *background = [command[@"background"] isKindOfClass:NSString.class]
            ? command[@"background"] : @"black";
        NSString *line = [command[@"line"] isKindOfClass:NSString.class]
            ? command[@"line"] : @"white";
        if (displayID.length &&
            ([style isEqualToString:@"off"] || [style isEqualToString:@"opaque"] ||
             [style isEqualToString:@"translucent"]) &&
            ([background isEqualToString:@"black"] || [background isEqualToString:@"white"]) &&
            ([line isEqualToString:@"black"] || [line isEqualToString:@"white"] ||
             [line isEqualToString:@"red"] || [line isEqualToString:@"blue"] ||
             [line isEqualToString:@"green"])) {
            self.mappingStyles[displayID] = @{
                @"enabled": style, @"background": background, @"line": line,
            };
            [self synchronizePanels];
        }
    } else if ([action isEqualToString:@"quit"]) {
        self.visible = NO;
        [self orderPanelsOut];
        [NSApp terminate:nil];
    }
}

- (void)setIdentifyColorFromHex:(id)value {
    NSString *hex = [value isKindOfClass:NSString.class] ? value : @"";
    hex = [hex stringByTrimmingCharactersInSet:
        [NSCharacterSet characterSetWithCharactersInString:@"#"]];
    unsigned int rgb = 0;
    NSScanner *scanner = [NSScanner scannerWithString:hex];
    if ([scanner scanHexInt:&rgb] && scanner.isAtEnd && hex.length == 6) {
        self.identifyColor = [NSColor colorWithRed:((rgb >> 16) & 0xff) / 255.0
                                             green:((rgb >> 8) & 0xff) / 255.0
                                              blue:(rgb & 0xff) / 255.0 alpha:1.0];
    }
}

- (void)displayParametersChanged:(NSNotification *)notification {
    // AppKit emits this for display attach/detach and arrangement changes.
    // Reuse the existing panels where possible; never relaunch the helper.
    [self synchronizePanels];
}

- (void)synchronizePanels {
    NSMutableDictionary<NSString *, NSDictionary *> *groupByDisplayID = NSMutableDictionary.dictionary;
    for (NSDictionary *group in self.groups) {
        for (id displayID in group[@"displayIDs"] ?: @[]) {
            groupByDisplayID[[displayID description]] = group;
        }
    }

    NSMutableSet<NSString *> *activeIDs = NSMutableSet.set;
    for (NSScreen *screen in NSScreen.screens) {
        NSString *displayID = displayIDForScreen(screen);
        if (!displayID.length) continue;
        [activeIDs addObject:displayID];
        NSDictionary *group = groupByDisplayID[displayID];
        NSArray<NSDictionary *> *members = [group[@"members"] isKindOfClass:NSArray.class]
            ? group[@"members"] : nil;
        if (!members.count) {
            NSString *name = screen.localizedName.length ? screen.localizedName : @"Display";
            members = @[@{@"name": name, @"friendlyName": @""}];
        }

        NSPanel *panel = self.panels[displayID];
        if (!panel) {
            NSRect localRect = NSMakeRect(0, 0, NSWidth(screen.frame), NSHeight(screen.frame));
            IdentifyOutlineView *view = [[IdentifyOutlineView alloc] initWithFrame:localRect];
            panel = [[NSPanel alloc] initWithContentRect:localRect
                styleMask:(NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel)
                backing:NSBackingStoreBuffered defer:NO screen:screen];
            panel.contentView = view;
            panel.backgroundColor = NSColor.clearColor;
            panel.opaque = NO;
            panel.hasShadow = NO;
            panel.ignoresMouseEvents = YES;
            panel.hidesOnDeactivate = NO;
            panel.floatingPanel = YES;
            panel.animationBehavior = NSWindowAnimationBehaviorNone;
            panel.level = NSScreenSaverWindowLevel;
            panel.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces |
                NSWindowCollectionBehaviorCanJoinAllApplications |
                NSWindowCollectionBehaviorFullScreenAuxiliary |
                NSWindowCollectionBehaviorStationary |
                NSWindowCollectionBehaviorIgnoresCycle;
            self.panels[displayID] = panel;
        } else {
            [panel setFrame:screen.frame display:NO animate:NO];
            panel.contentView.frame = NSMakeRect(0, 0, NSWidth(screen.frame), NSHeight(screen.frame));
        }
        IdentifyOutlineView *view = (IdentifyOutlineView *)panel.contentView;
        view.members = members;
        view.identifyColor = self.identifyColor ?: [NSColor colorWithRed:217.0 / 255.0
                                                                   green:154.0 / 255.0
                                                                    blue:62.0 / 255.0 alpha:1.0];
        view.mappingSettings = self.mappingStyles[displayID] ?: @{
            @"enabled": @"off", @"background": @"black", @"line": @"white",
        };
        view.identifyVisible = self.visible &&
            (!self.targetDisplayIDs || [self.targetDisplayIDs containsObject:displayID]);
        [panel.contentView setNeedsDisplay:YES];
        if (view.identifyVisible ||
            ![view.mappingSettings[@"enabled"] isEqualToString:@"off"])
            [panel orderFrontRegardless];
        else [panel orderOut:nil];
    }

    for (NSString *displayID in self.panels.allKeys.copy) {
        if (![activeIDs containsObject:displayID]) {
            [self.panels[displayID] orderOut:nil];
            [self.panels removeObjectForKey:displayID];
        }
    }
}

- (void)orderPanelsOut {
    for (NSPanel *panel in self.panels.allValues) [panel orderOut:nil];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    return NO;
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *application = NSApplication.sharedApplication;
        [application setActivationPolicy:NSApplicationActivationPolicyAccessory];
        IdentifyOverlayDelegate *delegate = IdentifyOverlayDelegate.new;
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
