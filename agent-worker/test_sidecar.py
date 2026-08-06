"""The whole suite, in one importable place.

The tests themselves live in tests/, one module per subject — this file is the
aggregator so that `python -m unittest test_sidecar` remains the single command
CI, the pre-commit hook, the wavetalk-test skill and CLAUDE.md all name.

Run from agent-worker/:  python -m unittest test_sidecar -v

Deliberately stdlib-only (unittest, tempfile) so the venv needs nothing new.
Network is never touched.
"""

from __future__ import annotations

# Sets LOG_TO_FILE before any module that logs is imported. Must come first.
import tests  # noqa: F401

from tests.test_settings import (  # noqa: F401
    TestAConfigValueCannotNameAFileOnTheDisk,
    TestNoSettingIsSmuggledThroughTheEnvironment,
    TestOneSettingReplacingAnotherSaysSo,
    TestSettings,
    TestSettingsThatAreOnlyWrongTogether,
    TestTheDataDirCheckCannotStopTheWorker,
    TestTurnTakingDelaysAreOptOut,
    TestTurnTakingSettingsReachTheCall,
    TestUploadedSoundsCannotFillTheVolume,
)
from tests.test_secrets_and_auth import (  # noqa: F401
    TestAdminAuth,
    TestAnUnreadablePasswordStoreFailsClosed,
    TestFirstRunIsNotOpenToTheWeb,
    TestFrontDoorPolicy,
    TestSecrets,
    TestStoredKeysStayHome,
    TestWrittenFilesGetExplicitModes,
)
from tests.test_http import (  # noqa: F401
    TestAnUnsignedWebhookCannotFillMemory,
    TestCallerIdentityCannotBeChosen,
    TestCallerIdentitySurvivesTwoProxies,
    TestHttpSurface,
    TestJoinTokensExpire,
    TestUsageControls,
)
from tests.test_widget import (  # noqa: F401
    TestAssetVersioning,
    TestEachSurfaceIsAnsweredDeliberately,
    TestPanelLoadsOnOpen,
    TestPanelMarkup,
    TestSoundPacks,
    TestTheCallButtonSaysWhatTheOperatorChose,
    TestTheStatusChipDescribesTheCallNotTheSDK,
    TestWidgetServerContract,
)
from tests.test_call_record import (  # noqa: F401
    TestCallPrivacy,
    TestCallRecord,
    TestStaleRecordsCanBeThrownAway,
    TestTheCallerGetsAVerdict,
    TestTheCallRecordHearsBothSides,
    TestTheCallRecordSaysWhoRang,
)
from tests.test_call_flow import (  # noqa: F401
    TestACallerWhoWasNeverHeardIsToldSo,
    TestALineThatFailsToGenerateIsStillSpoken,
    TestBackgroundWorkIsNotGarbageCollected,
    TestCallRecordTimestamps,
    TestCallStructure,
    TestComingBackFromAirIsAnnounced,
    TestEndingACallDisconnectsTheCaller,
    TestNothingToSay,
    TestSilentCallIsRecorded,
    TestTheAirGuardHoldsTheCallDJBack,
    TestTheCloseReasonIsReadable,
    TestTheIdleClockDoesNotRunWhileTheDJIsHeldBack,
)
from tests.test_tools_surface import (  # noqa: F401
    TestActionsAllHaveAReceipt,
    TestExposedSurface,
    TestStationActionResults,
    TestStationWideTools,
    TestTellingTheCallerWhenTheirSongPlays,
    TestTheDJDescribesRecordsItHasInformationAbout,
)
from tests.test_tools_logic import (  # noqa: F401
    TestAMoodIsNotASearch,
    TestMainToolLogic,
    TestSearchingForWhatTheCallerActuallySaid,
)
from tests.test_takeover import (  # noqa: F401
    TestCancellingATakeover,
    TestNamingAShowTheCallerSaid,
    TestPinningAShow,
    TestTheCallerCannotDoThisAllNight,
    TestTheStationEndpointsAreTheOnesUpstreamServes,
)
from tests.test_brain import (  # noqa: F401
    TestACallerCanBeToldNothingIsKept,
    TestBrainSplit,
    TestCallerContext,
    TestOneBadTrackCannotSwallowThePrompt,
    TestPromptAssembly,
    TestPrompts,
    TestSpeechFilter,
    TestTheConductHarnessCannotReachTheRealStation,
)
from tests.test_station import (  # noqa: F401
    TestABadPlaylistStaysSmall,
    TestStationConfig,
    TestTheCardCacheHasOneHome,
    TestTheDJKnowsWhoIsInTheBoothAndWhatTheShowPlays,
    TestTheDJKnowsWhoIsListening,
    TestTheHoldMatchesHowLongTheStationWillTalk,
    TestTheLiveShowRecordSurvivesTheScheduleLookup,
    TestTuneIn,
)
from tests.test_webhooks import (  # noqa: F401
    TestADeliveredPushIsProvedRatherThanAssumed,
    TestARefusedRegistrationSaysWhichFieldWasWrong,
    TestOtherWebhookRowsSurviveOurRegistration,
    TestOurWebhookRowKeepsItsIdentity,
    TestPointingAtANewStationRegistersAgain,
    TestTheRegistrationShapeIsTheOneTheStationReads,
    TestWeOnlyAskForEventsTheStationKnows,
)
from tests.test_voice import (  # noqa: F401
    TestADeclaredSampleRateIsMeasuredNotTrusted,
    TestAVoiceTheBackendCannotSpeakIsNotSilence,
    TestEveryPersonaIsCheckedNotOnlyTheOneOnAir,
    TestTheSttModelIsLoadedOnceForTheWholeProcess,
    TestVoiceDiscoveryIsNotHardcodedToOneShape,
    TestWhatTheBackendSaidReachesTheOperator,
)
from tests.test_docs import (  # noqa: F401
    TestTheDocsKeepUpWithTheCode,
)
from tests.test_house_rules import (  # noqa: F401
    TestEverySkillWouldActuallyLoad,
    TestNewCodeDoesNotArriveUntested,
    TestNoFileGrowsWithoutSomebodyDeciding,
    TestTheCommitGateIsStillWiredUp,
    TestTheLogKeepsTheLinesThatMatter,
    TestTheRoutingTableIsInOnePlace,
    TestTheSuiteIsNotQuietlyNotRunning,
    TestTheWrittenInstructionsStillDescribeTheCode,
)


if __name__ == "__main__":
    import unittest

    unittest.main(verbosity=2)
