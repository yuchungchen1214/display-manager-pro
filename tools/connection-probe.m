#import <Foundation/Foundation.h>
#import <QuartzCore/QuartzCore.h>
#import <CoreGraphics/CoreGraphics.h>
#import <ColorSync/ColorSync.h>
#import <objc/message.h>
#import <dlfcn.h>

// Only explicitly named, zero-argument getters are invoked. Private API absence
// and exceptions remain visible in the report; no display setters are used.
static id readValue(id object, NSString *key) {
    if (![object respondsToSelector:NSSelectorFromString(key)]) return NSNull.null;
    @try { return [object valueForKey:key] ?: NSNull.null; }
    @catch(NSException *e) { return @{ @"error":e.reason ?: e.name }; }
}
static id jsonValue(id value) {
    if ([value isKindOfClass:NSString.class] || [value isKindOfClass:NSNumber.class] || value == NSNull.null) return value;
    if ([value isKindOfClass:NSData.class]) return @{ @"base64":[value base64EncodedStringWithOptions:0] };
    if ([value isKindOfClass:NSArray.class]) { NSMutableArray *a=[NSMutableArray array]; for(id v in value) [a addObject:jsonValue(v)]; return a; }
    if ([value isKindOfClass:NSDictionary.class]) { NSMutableDictionary *d=[NSMutableDictionary dictionary]; for(id k in value) d[[k description]]=jsonValue(value[k]); return d; }
    return [value description] ?: NSNull.null;
}
static NSDictionary *desktopModeRecord(CGDisplayModeRef mode) {
    return @{ @"width":@(CGDisplayModeGetWidth(mode)), @"height":@(CGDisplayModeGetHeight(mode)),
        @"pixelWidth":@(CGDisplayModeGetPixelWidth(mode)), @"pixelHeight":@(CGDisplayModeGetPixelHeight(mode)),
        @"refreshRate":@(CGDisplayModeGetRefreshRate(mode)),
        @"modeID":[NSString stringWithFormat:@"%u",CGDisplayModeGetIODisplayModeID(mode)],
        @"ioFlags":@(CGDisplayModeGetIOFlags(mode)),
        @"usableForDesktopGUI":@(CGDisplayModeIsUsableForDesktopGUI(mode)),
        @"source":@"CoreGraphics.CGDisplayMode" };
}
static NSDictionary *modeRecord(id mode) {
    if(!mode || mode==NSNull.null || [mode isKindOfClass:NSDictionary.class]) return @{ @"status":@"unavailable" };
    NSMutableDictionary *r=[NSMutableDictionary dictionary];
    for(NSString *k in @[@"width",@"height",@"refreshRate",@"isVRR",@"bitDepth",@"colorMode",@"hdrMode",@"colorGamut",@"isVirtual",@"pixelAspectRatio",@"preferredScale",@"maximumSourceWidth",@"maximumSourceHeight",@"maximumSourceBandwidth",@"description"])
        r[k]=jsonValue(readValue(mode,k));
    id ident=readValue(mode,@"internalRepresentation");
    r[@"modeID"]=[ident isKindOfClass:NSNumber.class] ? [ident stringValue] : ident;
    r[@"source"]=@"QuartzCore.CADisplayMode";
    return r;
}
int main(int argc,const char **argv) { @autoreleasepool {
    BOOL fullDetails = argc > 1 && strcmp(argv[1], "--full") == 0;
    BOOL liveState = argc > 1 && strcmp(argv[1], "--live") == 0;
    Class cls=NSClassFromString(@"CADisplay");
    SEL selector=NSSelectorFromString(@"displays");
    if(!cls || ![cls respondsToSelector:selector]) { fprintf(stderr,"CADisplay.displays unavailable on this macOS\n"); return 2; }
    NSArray *screens=((id(*)(id,SEL))objc_msgSend)(cls,selector);
    NSMutableArray *records=[NSMutableArray array];
    for(id screen in screens) {
        NSMutableDictionary *r=[NSMutableDictionary dictionary];
        for(NSString *key in @[@"displayId",@"name",@"productName",@"uniqueId",@"deviceName",@"isExternal",@"transportType",@"externalDisplayAttributes",@"supportedHDRModes",@"connectionSeed",@"seed"])
            r[key]=jsonValue(readValue(screen,key));
        r[@"currentMode"]=modeRecord(readValue(screen,@"currentMode"));
        if (!liveState) r[@"preferredMode"]=modeRecord(readValue(screen,@"preferredMode"));
        NSMutableArray *modes=[NSMutableArray array];
        id available=liveState ? nil : readValue(screen,@"availableModes");
        if([available isKindOfClass:NSArray.class]) for(id m in available) [modes addObject:modeRecord(m)];
        r[@"availableModes"]=modes;
        if (!liveState) {
            r[@"availableModesStatus"]=[available isKindOfClass:NSArray.class]?@"read":@"unavailable";
            r[@"connectionSeedAfter"]=jsonValue(readValue(screen,@"connectionSeed"));
            r[@"currentModeAfter"]=modeRecord(readValue(screen,@"currentMode"));
            r[@"snapshotStable"]=@([r[@"connectionSeed"] isEqual:r[@"connectionSeedAfter"]] && [r[@"currentMode"] isEqual:r[@"currentModeAfter"]]);
        }
        CGDirectDisplayID did=[r[@"displayId"] unsignedIntValue];
        r[@"cgOnline"]=@(CGDisplayIsOnline(did));
        r[@"cgActive"]=@(CGDisplayIsActive(did));
        r[@"cgBuiltIn"]=@(CGDisplayIsBuiltin(did));
        r[@"cgVendorID"]=@(CGDisplayVendorNumber(did));
        r[@"cgProductID"]=@(CGDisplayModelNumber(did));
        r[@"cgSerialNumber"]=@(CGDisplaySerialNumber(did));
        CGRect bounds=CGDisplayBounds(did);
        r[@"desktop"]=@{ @"x":@(bounds.origin.x),@"y":@(bounds.origin.y),
            @"width":@(bounds.size.width),@"height":@(bounds.size.height),
            @"rotation":@(CGDisplayRotation(did)),@"main":@(CGDisplayIsMain(did)),
            @"mirrorsDisplayID":@(CGDisplayMirrorsDisplay(did)) };
        CGDisplayModeRef desktop=CGDisplayCopyDisplayMode(did);
        if (desktop) {
            r[@"framebufferMode"]=desktopModeRecord(desktop);
            CGDisplayModeRelease(desktop);
        }
        if (fullDetails) {
        NSDictionary *options=@{ (__bridge NSString *)kCGDisplayShowDuplicateLowResolutionModes:@YES };
        CFArrayRef desktopModes=CGDisplayCopyAllDisplayModes(did,(__bridge CFDictionaryRef)options);
        if (desktopModes) {
            NSMutableArray *entries=NSMutableArray.new;
            for (CFIndex i=0;i<CFArrayGetCount(desktopModes);i++)
                [entries addObject:desktopModeRecord((CGDisplayModeRef)CFArrayGetValueAtIndex(desktopModes,i))];
            r[@"desktopModes"]=entries;
            CFRelease(desktopModes);
        }
        }
        ColorSyncProfileRef profile=ColorSyncProfileCreateWithDisplayID(did);
        if (profile) {
            CFURLRef url=ColorSyncProfileGetURL(profile,NULL);
            CFStringRef description=ColorSyncProfileCopyDescriptionString(profile);
            NSMutableDictionary *profileInfo=[@{ @"status":@"read",@"source":@"ColorSyncProfileCreateWithDisplayID",
                @"description":description?CFBridgingRelease(description):NSNull.null,
                @"url":url?[(__bridge NSURL*)url absoluteString]:NSNull.null } mutableCopy];
            if (fullDetails) {
                CFDataRef data=ColorSyncProfileCopyData(profile,NULL);
                profileInfo[@"data"]=data?jsonValue(CFBridgingRelease(data)):NSNull.null;
            }
            r[@"colorProfile"]=profileInfo;
            CFRelease(profile);
        } else r[@"colorProfile"]=@{ @"status":@"unavailable",@"source":@"ColorSyncProfileCreateWithDisplayID" };
        CFUUIDRef uuid=CGDisplayCreateUUIDFromDisplayID(did);
        if(uuid) { r[@"cgUUID"]=CFBridgingRelease(CFUUIDCreateString(NULL,uuid)); CFRelease(uuid); }
        void *handle=liveState ? NULL : dlopen("/System/Library/Frameworks/CoreDisplay.framework/CoreDisplay",RTLD_LAZY);
        if(handle) {
            CFDictionaryRef (*info)(CGDirectDisplayID)=dlsym(handle,"CoreDisplay_DisplayCreateInfoDictionary");
            if(info) {
                CFDictionaryRef raw=info(did);
                if(raw) {
                    NSMutableDictionary *coreInfo=[jsonValue(CFBridgingRelease(raw)) mutableCopy];
                    id productName=coreInfo[@"DisplayProductName"];
                    if([productName isKindOfClass:NSDictionary.class]) {
                        id englishName=productName[@"en_US"] ?: productName[@"en_GB"];
                        if([englishName isKindOfClass:NSString.class]) coreInfo[@"DisplayProductName"]=englishName;
                        else [coreInfo removeObjectForKey:@"DisplayProductName"];
                    }
                    r[@"coreDisplayInfo"]=coreInfo;
                }
            }
            dlclose(handle);
        }
        [records addObject:r];
    }
    NSDictionary *report=@{@"schemaVersion":@1,@"capturedAt":[NSISO8601DateFormatter.new stringFromDate:NSDate.date],@"macOS":NSProcessInfo.processInfo.operatingSystemVersionString,@"source":@"QuartzCore private CADisplay API (driver-reported state, not a physical signal measurement)",@"displays":records};
    NSError *error=nil;
    NSData *data=[NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted|NSJSONWritingSortedKeys error:&error];
    if(!data) { fprintf(stderr,"%s\n",error.description.UTF8String); return 1; }
    if((argc==2 && !fullDetails && !liveState) || argc==3) {
        const char *outputPath = argv[argc-1];
        if(![data writeToFile:[NSString stringWithUTF8String:outputPath] options:NSDataWritingAtomic error:&error]) { fprintf(stderr,"%s\n",error.description.UTF8String); return 1; }
    }
    else fwrite(data.bytes,1,data.length,stdout);
    return 0;
} }
